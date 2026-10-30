-- Parse literal identities once per worker; JWT verification has already succeeded.
local function identities(name, pattern)
  local raw = os.getenv(name) or ""
  local values = {}
  if raw ~= "" then
    for entry in (raw .. ","):gmatch("(.-),") do
      entry = entry:match("^%s*(.-)%s*$")
      assert(entry:match(pattern), name .. " must be a comma-separated list of literal identities")
      values[#values + 1] = entry
    end
  end
  return values
end
local emails = identities("CF_ACCESS_ALLOWED_EMAILS", "^[^%s%c,@]+@[^%s%c,@]+$")
local services = identities("CF_ACCESS_ALLOWED_SERVICE_TOKENS", "^[A-Za-z0-9_][A-Za-z0-9_%.@%-]*$")
local function contains(values, claim)
  if type(claim) ~= "string" then return false end
  for _, value in ipairs(values) do
    if claim == value then return true end
  end
  return false
end
function envoy_on_request(handle)
  if #emails == 0 and #services == 0 then return end
  local verified = handle:streamInfo():dynamicMetadata():get("envoy.filters.http.jwt_authn") or {}
  local claims = verified.claims or {}
  if not contains(emails, claims.email) and not contains(services, claims.common_name) then
    handle:respond({[":status"] = "403"}, "Access identity is not allowed")
  end
end
