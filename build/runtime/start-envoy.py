"""Render JSON string values safely and replace the launcher with Envoy."""
import json
import copy
import os
import re
import sys
from pathlib import Path


def render(template, environment):
    # Replacing within parsed strings lets json.dumps escape user input, and
    # leaves Lua patterns and Envoy format specifiers untouched.
    values = {name: environment.get(name, '') for name in
              ('CF_ACCESS_TEAM_DOMAIN', 'CF_ACCESS_AUD')}

    def substitute(value):
        if isinstance(value, str):
            for name, replacement in values.items():
                value = value.replace('${' + name + ':-}', replacement)
            return value
        if isinstance(value, list):
            return [substitute(item) for item in value]
        if isinstance(value, dict):
            return {key: substitute(item) for key, item in value.items()}
        return value

    config = substitute(json.loads(template))
    manager = config['static_resources']['listeners'][0]['filter_chains'][0]['filters'][0]['typed_config']
    mappers = manager['local_reply_config']['mappers']
    # JWT failures use 401 internally; render the same public 403 page once.
    status = lambda mapper: mapper['filter']['status_code_filter']['comparison']['value']['default_value']
    if not any(status(mapper) == 401 for mapper in mappers):
        denied = copy.deepcopy(next(mapper for mapper in mappers if status(mapper) == 403))
        denied['filter']['status_code_filter']['comparison']['value']['default_value'] = 401
        mappers.insert(0, denied)
    return config


def validate(environment):
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.cloudflareaccess\.com',
                        environment.get('CF_ACCESS_TEAM_DOMAIN', '')):
        raise ValueError('Set CF_ACCESS_TEAM_DOMAIN to your team.cloudflareaccess.com hostname')
    if not re.fullmatch(r'[a-fA-F0-9]{64}', environment.get('CF_ACCESS_AUD', '')):
        raise ValueError('Set CF_ACCESS_AUD to the 64-character application audience')


if __name__ == '__main__':
    try:
        validate(os.environ)
    except ValueError as error:
        sys.exit(str(error))
    config = render(Path('/etc/envoy/envoy.json.template').read_text(), os.environ)
    path = Path('/run/envoy/envoy.json')
    Path('/run/envoy/admin.sock').unlink(missing_ok=True)
    # A previous launch leaves a read-only config behind.
    path.unlink(missing_ok=True)
    with path.open('x') as output:
        os.chmod(path, 0o400)
        json.dump(config, output)
    os.execv('/usr/local/bin/envoy', ['envoy', '-c', str(path), '--concurrency', '2',
                                   '--disable-hot-restart'])
