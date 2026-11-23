local enabled = (os.getenv("AIO_RELEASE_TAG") or "") ~= ""
local cookie_prefix = "__Host-aio-update-notice-"

local function notice_day(value)
  local day, tag = value:match("^(%d%d%d%d%-%d%d%-%d%d)_(.+)$")
  if tag and (tag:match("^v%d+%.%d+%.%d+$") or tag:match("^v%d+%.%d+%.%d+%-[1-9]%d*$")) then
    return day
  end
end

function envoy_on_request(handle)
  if not enabled then return end
  local headers = handle:headers()
  local path = headers:get(":path") or ""
  if (path ~= "/" and path:sub(1, 2) ~= "/?")
      or headers:get("sec-fetch-mode") ~= "navigate" or headers:get("sec-fetch-dest") ~= "document"
      or headers:get("upgrade") or headers:get("sec-purpose") or headers:get("purpose") then
    return
  end

  -- The JWT and identity filters have already accepted this request.
  local today = os.date("!%Y-%m-%d")
  local method = headers:get(":method")
  local length = tonumber(headers:get("content-length") or "")
  if method == "POST" and headers:get("sec-fetch-site") == "same-origin"
      and headers:get("content-type") == "application/x-www-form-urlencoded"
      and length and length > 0 and length <= 128 then
    local body = handle:body()
    local value = body and body:getBytes(0, body:length()):match("^aio%-update%-notice=(.+)$")
    local day = value and notice_day(value)
    if day then
      local response = {[":status"] = "303", ["location"] = path, ["cache-control"] = "no-store"}
      if day == today then
        response["set-cookie"] = cookie_prefix .. value .. "=1; Path=/; Max-Age=86400; Secure; HttpOnly; SameSite=Lax"
      end
      handle:respond(response)
      return
    end
  end
  if method ~= "GET" then return end

  local dismissals = {}
  local cookies = ";" .. (headers:get("cookie") or "")
  for value, marker in cookies:gmatch(";%s*__Host%-aio%-update%-notice%-([^=;]+)=([^;]*)") do
    if marker == "1" and notice_day(value) == today then
      dismissals[#dismissals + 1] = value
    end
  end
  local response, body = handle:httpCall("update_notice", {
    [":method"] = "GET", [":path"] = "/", [":authority"] = "update-notice",
    ["x-aio-dismissed-notices"] = table.concat(dismissals, " ")
  }, nil, 150)
  if response and response[":status"] == "200" and body then
    handle:respond({
      [":status"] = "200",
      ["content-type"] = "text/html; charset=utf-8",
      ["cache-control"] = "no-store",
      ["referrer-policy"] = "no-referrer",
      ["x-content-type-options"] = "nosniff",
      ["content-security-policy"] = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    }, body)
  end
end
