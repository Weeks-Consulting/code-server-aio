local enabled = (os.getenv("AIO_RELEASE_TAG") or "") ~= ""

function envoy_on_request(handle)
  if not enabled then return end
  local headers = handle:headers()
  local path = headers:get(":path") or ""
  if headers:get(":method") ~= "GET" or (path ~= "/" and path:sub(1, 2) ~= "/?")
      or headers:get("sec-fetch-mode") ~= "navigate" or headers:get("sec-fetch-dest") ~= "document"
      or headers:get("upgrade") or headers:get("sec-purpose") or headers:get("purpose") then
    return
  end

  -- The JWT and identity filters have already accepted this request.
  local verified = handle:streamInfo():dynamicMetadata():get("envoy.filters.http.jwt_authn") or {}
  local claims = verified.claims or {}
  local subject = claims.sub
  if type(subject) ~= "string" or subject == "" then return end

  local response, body = handle:httpCall("update_notice", {
    [":method"] = "POST", [":path"] = "/", [":authority"] = "update-notice"
  }, subject, 150)
  if response and response[":status"] == "200" and body then
    handle:respond({
      [":status"] = "200",
      ["content-type"] = "text/html; charset=utf-8",
      ["cache-control"] = "no-store",
      ["referrer-policy"] = "no-referrer",
      ["x-content-type-options"] = "nosniff",
      ["content-security-policy"] = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"
    }, body)
  end
end
