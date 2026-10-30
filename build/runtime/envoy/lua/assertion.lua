-- Validate startup settings without interpolating user data into Lua source.
local domain = os.getenv("CF_ACCESS_TEAM_DOMAIN") or ""
local team = domain:match("^(.-)%.cloudflareaccess%.com$")
assert(team and (team:match("^[a-z0-9]$") or team:match("^[a-z0-9][a-z0-9%-]*[a-z0-9]$")),
       "Set CF_ACCESS_TEAM_DOMAIN to your team.cloudflareaccess.com hostname")
local audience = os.getenv("CF_ACCESS_AUD") or ""
assert(#audience == 64 and audience:match("^[a-fA-F0-9]+$"),
       "Set CF_ACCESS_AUD to the 64-character application audience")
function envoy_on_request(handle)
  local headers = handle:headers()
  local assertions = headers:getNumValues("cf-access-jwt-assertion")
  if assertions ~= 1 then
    local metadata = handle:streamInfo():dynamicMetadata()
    metadata:set("workspace.access", "denial_reason",
                 assertions == 0 and "missing_assertion" or "multiple_assertions")
    -- Only this exact local probe's missing-token rejection may be quiet.
    -- Use the physical peer, never a client-supplied forwarding header.
    local peer = handle:streamInfo():downstreamDirectRemoteAddress()
    if assertions == 0 and peer:match("^127%.0%.0%.1:%d+$")
        and headers:get(":method") == "GET" and headers:get(":path") == "/"
        and headers:get("user-agent") == "code-server-container-healthcheck" then
      metadata:set("workspace.access", "quiet_healthcheck", true)
    end
    handle:respond({[":status"] = "403"}, "A single Access JWT header is required")
  end
end
