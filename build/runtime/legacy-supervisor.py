"""Run Supervisor when a saved Compose file still starts Docker's init."""

import signal

from supervisor import supervisord
from supervisor.options import ServerOptions


class LegacyServerOptions(ServerOptions):
    def setsignals(self):
        super().setsignals()
        # Old snapshots inherit the new image's stop signal through docker-init.
        receive = self.signal_receiver.receive
        signal.signal(signal.SIGRTMIN + 3, lambda _, frame: receive(signal.SIGTERM, frame))


if __name__ == "__main__":
    supervisord.ServerOptions = LegacyServerOptions
    supervisord.main()
