local AssetService = game:GetService("AssetService")
local ChangeHistoryService = game:GetService("ChangeHistoryService")
local HttpService = game:GetService("HttpService")
local MaterialService = game:GetService("MaterialService")
local Selection = game:GetService("Selection")

local MeshSync = {}

local SCHEMA_ID = "roblox-mesh-sync/4"
local PREVIOUS_SCHEMA_ID = "roblox-mesh-sync/3"
local LEGACY_SCHEMA_ID = "roblox-mesh-sync/2"
local ORIGINAL_SCHEMA_ID = "roblox-mesh-sync/1"
local REVERSE_SCHEMA_ID = "roblox-mesh-sync-reverse/3"
local CSG_REVERSE_SCHEMA_ID = "roblox-mesh-sync-reverse/4"
local PLUGIN_VERSION = "0.11.2"
local MESH_CACHE_KEY = "RobloxMeshSync.MeshCache.v1"
local IMAGE_CACHE_KEY = "RobloxMeshSync.ImageCache.v1"
local CONNECTION_KEY = "RobloxMeshSync.Connection.v1"
local PORT_KEY = "RobloxMeshSync.Port.v1"
local RECORDING_KEY = "RobloxMeshSync.ActiveRecording.v1"
local MAX_BLOB_BYTES = 32 * 1024 * 1024
local MAX_INSTANCES = 10000
local CHUNK_BYTES = 256 * 1024
local DEFAULT_PORT = 27182

local function expectTable(value: any, path: string): { [any]: any }
	if type(value) ~= "table" then
		error(`{path} must be a table`)
	end
	return value
end

local function expectString(value: any, path: string): string
	if type(value) ~= "string" or value == "" then
		error(`{path} must be a non-empty string`)
	end
	return value
end

local function expectNumber(value: any, path: string): number
	if type(value) ~= "number" or value ~= value or value == math.huge or value == -math.huge then
		error(`{path} must be a finite number`)
	end
	return value
end

local function loadCache(plugin, key: string): { [string]: number }
	local raw = plugin:GetSetting(key)
	if type(raw) ~= "string" or raw == "" then
		return {}
	end
	local ok, decoded = pcall(HttpService.JSONDecode, HttpService, raw)
	if not ok or type(decoded) ~= "table" then
		return {}
	end
	local result = {}
	for hash, assetId in decoded do
		if type(hash) == "string" and type(assetId) == "number" then
			result[hash] = assetId
		end
	end
	return result
end

local function saveCache(plugin, key: string, cache: { [string]: number })
	plugin:SetSetting(key, HttpService:JSONEncode(cache))
end

local function assetUri(assetId: number): string
	return `rbxassetid://{assetId}`
end

local function parseConnection(text: string): (number, string)
	local portText, token = string.match(text, "^%s*(%d+)|([0-9a-fA-F]+)%s*$")
	local port = tonumber(portText)
	if not port or port < 1024 or port > 65535 or not token or #token < 16 then
		error("Connection code must be copied from Blender")
	end
	return port, token
end

local function colorFromArray(value: any, fallback: Color3): Color3
	if type(value) ~= "table" or #value < 3 then
		return fallback
	end
	return Color3.new(
		math.clamp(expectNumber(value[1], "color[1]"), 0, 1),
		math.clamp(expectNumber(value[2], "color[2]"), 0, 1),
		math.clamp(expectNumber(value[3], "color[3]"), 0, 1)
	)
end

local SHA256_K = {
	0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
	0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
	0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
	0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
	0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
	0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
	0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
	0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
}

local function add32(...: number): number
	local sum = 0
	for index = 1, select("#", ...) do
		sum = (sum + select(index, ...)) % 4294967296
	end
	return sum
end

local function sha256(value: string): string
	local bytes = table.create(#value + 72)
	for index = 1, #value do bytes[index] = string.byte(value, index) end
	local bitLength = #value * 8
	table.insert(bytes, 0x80)
	while #bytes % 64 ~= 56 do table.insert(bytes, 0) end
	local high = math.floor(bitLength / 4294967296)
	local low = bitLength % 4294967296
	for shift = 24, 0, -8 do table.insert(bytes, bit32.band(bit32.rshift(high, shift), 0xff)) end
	for shift = 24, 0, -8 do table.insert(bytes, bit32.band(bit32.rshift(low, shift), 0xff)) end

	local h = { 0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19 }
	for offset = 1, #bytes, 64 do
		local w = table.create(64, 0)
		for index = 0, 15 do
			local at = offset + index * 4
			w[index + 1] = bytes[at] * 0x1000000 + bytes[at + 1] * 0x10000 + bytes[at + 2] * 0x100 + bytes[at + 3]
		end
		for index = 17, 64 do
			local a = w[index - 15]
			local b = w[index - 2]
			local s0 = bit32.bxor(bit32.rrotate(a, 7), bit32.rrotate(a, 18), bit32.rshift(a, 3))
			local s1 = bit32.bxor(bit32.rrotate(b, 17), bit32.rrotate(b, 19), bit32.rshift(b, 10))
			w[index] = add32(w[index - 16], s0, w[index - 7], s1)
		end
		local a, b, c, d, e, f, g, hh = table.unpack(h)
		for index = 1, 64 do
			local s1 = bit32.bxor(bit32.rrotate(e, 6), bit32.rrotate(e, 11), bit32.rrotate(e, 25))
			local choose = bit32.bxor(bit32.band(e, f), bit32.band(bit32.bnot(e), g))
			local temp1 = add32(hh, s1, choose, SHA256_K[index], w[index])
			local s0 = bit32.bxor(bit32.rrotate(a, 2), bit32.rrotate(a, 13), bit32.rrotate(a, 22))
			local majority = bit32.bxor(bit32.band(a, b), bit32.band(a, c), bit32.band(b, c))
			local temp2 = add32(s0, majority)
			hh, g, f, e, d, c, b, a = g, f, e, add32(d, temp1), c, b, a, add32(temp1, temp2)
		end
		h[1], h[2], h[3], h[4] = add32(h[1], a), add32(h[2], b), add32(h[3], c), add32(h[4], d)
		h[5], h[6], h[7], h[8] = add32(h[5], e), add32(h[6], f), add32(h[7], g), add32(h[8], hh)
	end
	local output = table.create(8)
	for index, number in h do output[index] = string.format("%08x", number) end
	return table.concat(output)
end

local function makeUi(plugin)
	local toolbar = plugin:CreateToolbar("Primitive Sync")
	local toolbarButton = toolbar:CreateButton("MeshSync", "Connect Blender Mesh Sync", "")
	toolbarButton.ClickableWhenViewportHidden = true

	local widgetInfo = DockWidgetPluginGuiInfo.new(
		Enum.InitialDockState.Right,
		false,
		false,
		360,
		435,
		390,
		405
	)
	local widget = plugin:CreateDockWidgetPluginGuiAsync("RobloxMeshSyncWidgetV1", widgetInfo)
	widget.Title = `Blender Mesh Sync {PLUGIN_VERSION}`

	local root = Instance.new("Frame")
	root.BackgroundTransparency = 1
	root.Size = UDim2.fromScale(1, 1)
	root.Parent = widget

	local padding = Instance.new("UIPadding")
	padding.PaddingTop = UDim.new(0, 12)
	padding.PaddingBottom = UDim.new(0, 12)
	padding.PaddingLeft = UDim.new(0, 12)
	padding.PaddingRight = UDim.new(0, 12)
	padding.Parent = root

	local layout = Instance.new("UIListLayout")
	layout.Padding = UDim.new(0, 9)
	layout.SortOrder = Enum.SortOrder.LayoutOrder
	layout.Parent = root

	local title = Instance.new("TextLabel")
	title.BackgroundTransparency = 1
	title.Size = UDim2.new(1, 0, 0, 26)
	title.Font = Enum.Font.BuilderSansBold
	title.Text = "Blender Mesh Instancing"
	title.TextSize = 18
	title.TextXAlignment = Enum.TextXAlignment.Left
	title.TextColor3 = Color3.fromRGB(235, 235, 235)
	title.LayoutOrder = 1
	title.Parent = root

	local description = Instance.new("TextLabel")
	description.BackgroundTransparency = 1
	description.Size = UDim2.new(1, 0, 0, 38)
	description.Font = Enum.Font.BuilderSans
	description.Text = "接続コードは不要です。初回はBlenderで Allow Studio Connection を押してください。"
	description.TextWrapped = true
	description.TextSize = 13
	description.TextXAlignment = Enum.TextXAlignment.Left
	description.TextYAlignment = Enum.TextYAlignment.Top
	description.TextColor3 = Color3.fromRGB(190, 190, 190)
	description.LayoutOrder = 2
	description.Parent = root

	local savedConnection = plugin:GetSetting(CONNECTION_KEY)
	local savedConnectionPort = if type(savedConnection) == "string"
		then tonumber(string.match(savedConnection, "^%s*(%d+)|")) else nil
	local savedPort = plugin:GetSetting(PORT_KEY)
	local portBox = Instance.new("TextBox")
	portBox.Name = "Port"
	portBox.Size = UDim2.new(1, 0, 0, 30)
	portBox.BackgroundColor3 = Color3.fromRGB(45, 45, 45)
	portBox.BorderSizePixel = 0
	portBox.ClearTextOnFocus = false
	portBox.PlaceholderText = "Port (example: 27182)"
	portBox.Text = tostring(if type(savedPort) == "number" then savedPort else savedConnectionPort or DEFAULT_PORT)
	portBox.TextColor3 = Color3.fromRGB(235, 235, 235)
	portBox.TextSize = 13
	portBox.LayoutOrder = 3
	portBox.Parent = root

	local connectionBox = Instance.new("TextBox")
	connectionBox.Name = "ConnectionCode"
	connectionBox.Size = UDim2.new(1, 0, 0, 34)
	connectionBox.BackgroundColor3 = Color3.fromRGB(45, 45, 45)
	connectionBox.BorderSizePixel = 0
	connectionBox.ClearTextOnFocus = false
	connectionBox.PlaceholderText = "27182|connection-token"
	connectionBox.Text = savedConnection or ""
	connectionBox.TextColor3 = Color3.fromRGB(235, 235, 235)
	connectionBox.TextSize = 13
	connectionBox.LayoutOrder = 3
	connectionBox.Visible = false
	connectionBox.Parent = root

	local connectButton = Instance.new("TextButton")
	connectButton.Size = UDim2.new(1, 0, 0, 36)
	connectButton.BackgroundColor3 = Color3.fromRGB(0, 162, 255)
	connectButton.BorderSizePixel = 0
	connectButton.Font = Enum.Font.BuilderSansBold
	connectButton.Text = "Connect"
	connectButton.TextSize = 15
	connectButton.TextColor3 = Color3.new(1, 1, 1)
	connectButton.LayoutOrder = 4
	connectButton.Parent = root

	local forgetButton = Instance.new("TextButton")
	forgetButton.Size = UDim2.new(1, 0, 0, 28)
	forgetButton.BackgroundColor3 = Color3.fromRGB(65, 65, 65)
	forgetButton.BorderSizePixel = 0
	forgetButton.Font = Enum.Font.BuilderSans
	forgetButton.Text = "Forget Connection"
	forgetButton.TextSize = 12
	forgetButton.TextColor3 = Color3.fromRGB(215, 215, 215)
	forgetButton.LayoutOrder = 5
	forgetButton.Parent = root

	local syncButton = Instance.new("TextButton")
	syncButton.Size = UDim2.new(1, 0, 0, 32)
	syncButton.BackgroundColor3 = Color3.fromRGB(70, 70, 70)
	syncButton.BorderSizePixel = 0
	syncButton.Font = Enum.Font.BuilderSansBold
	syncButton.Text = "Sync Current Revision"
	syncButton.TextSize = 14
	syncButton.TextColor3 = Color3.new(1, 1, 1)
	syncButton.LayoutOrder = 6
	syncButton.Parent = root

	local exportButton = Instance.new("TextButton")
	exportButton.Size = UDim2.new(1, 0, 0, 32)
	exportButton.BackgroundColor3 = Color3.fromRGB(56, 118, 82)
	exportButton.BorderSizePixel = 0
	exportButton.Font = Enum.Font.BuilderSansBold
	exportButton.Text = "Send Selection to Blender"
	exportButton.TextSize = 14
	exportButton.TextColor3 = Color3.new(1, 1, 1)
	exportButton.LayoutOrder = 8
	exportButton.Parent = root

	local transformFrame = Instance.new("Frame")
	transformFrame.Name = "ReverseTransformChannels"
	transformFrame.BackgroundTransparency = 1
	transformFrame.Size = UDim2.new(1, 0, 0, 32)
	transformFrame.LayoutOrder = 7
	transformFrame.Parent = root
	local transformLayout = Instance.new("UIListLayout")
	transformLayout.FillDirection = Enum.FillDirection.Horizontal
	transformLayout.Padding = UDim.new(0, 6)
	transformLayout.SortOrder = Enum.SortOrder.LayoutOrder
	transformLayout.Parent = transformFrame

	local function makeTransformToggle(label: string, settingKey: string, order: number): TextButton
		local button = Instance.new("TextButton")
		button.Size = UDim2.new(1 / 3, -4, 1, 0)
		button.BorderSizePixel = 0
		button.Font = Enum.Font.BuilderSans
		button.TextSize = 12
		button.LayoutOrder = order
		button.Parent = transformFrame
		local enabled = plugin:GetSetting(settingKey) ~= false
		local function refresh()
			button:SetAttribute("ChannelEnabled", enabled)
			button.Text = `{if enabled then "✓" else "−"} {label}`
			button.BackgroundColor3 = if enabled then Color3.fromRGB(56, 118, 82) else Color3.fromRGB(65, 65, 65)
			button.TextColor3 = Color3.new(1, 1, 1)
		end
		button.MouseButton1Click:Connect(function()
			enabled = not enabled
			plugin:SetSetting(settingKey, enabled)
			refresh()
		end)
		local corner = Instance.new("UICorner")
		corner.CornerRadius = UDim.new(0, 6)
		corner.Parent = button
		refresh()
		return button
	end
	local positionToggle = makeTransformToggle("Position", "RobloxMeshSync.ReversePosition.v1", 1)
	local rotationToggle = makeTransformToggle("Rotation", "RobloxMeshSync.ReverseRotation.v1", 2)
	local scaleToggle = makeTransformToggle("Scale", "RobloxMeshSync.ReverseScale.v1", 3)

	for _, button in { connectButton, forgetButton, syncButton, exportButton } do
		local corner = Instance.new("UICorner")
		corner.CornerRadius = UDim.new(0, 6)
		corner.Parent = button
	end

	local statusLabel = Instance.new("TextLabel")
	statusLabel.BackgroundTransparency = 1
	statusLabel.Size = UDim2.new(1, 0, 0, 58)
	statusLabel.Font = Enum.Font.BuilderSans
	statusLabel.Text = "Not connected"
	statusLabel.TextWrapped = true
	statusLabel.TextSize = 13
	statusLabel.TextXAlignment = Enum.TextXAlignment.Left
	statusLabel.TextYAlignment = Enum.TextYAlignment.Top
	statusLabel.TextColor3 = Color3.fromRGB(180, 180, 180)
	statusLabel.LayoutOrder = 10
	statusLabel.Parent = root

	toolbarButton.Click:Connect(function()
		widget.Enabled = not widget.Enabled
		toolbarButton:SetActive(widget.Enabled)
	end)
	widget:GetPropertyChangedSignal("Enabled"):Connect(function()
		toolbarButton:SetActive(widget.Enabled)
	end)

	return {
		widget = widget,
		portBox = portBox,
		connectionBox = connectionBox,
		connectButton = connectButton,
		forgetButton = forgetButton,
		syncButton = syncButton,
		exportButton = exportButton,
		statusLabel = statusLabel,
		positionToggle = positionToggle,
		rotationToggle = rotationToggle,
		scaleToggle = scaleToggle,
	}
end

function MeshSync.start(plugin)
	local staleRecording = plugin:GetSetting(RECORDING_KEY)
	if type(staleRecording) == "string" and staleRecording ~= "" then
		pcall(function()
			if ChangeHistoryService:IsRecordingInProgress(staleRecording) then
				ChangeHistoryService:FinishRecording(staleRecording, Enum.FinishRecordingOperation.Cancel)
			end
		end)
		plugin:SetSetting(RECORDING_KEY, "")
	end
	local ui = makeUi(plugin)
	local meshCache = loadCache(plugin, MESH_CACHE_KEY)
	local imageCache = loadCache(plugin, IMAGE_CACHE_KEY)
	local connected = false
	local connecting = false
	local autoReconnect = type(ui.connectionBox.Text) == "string" and ui.connectionBox.Text ~= ""
	local stopped = false
	local importing = false
	local connectionLost = false
	local port = 0
	local token = ""
	local lastRevision = 0
	local lastAnnouncedRevision = 0

	local function setStatus(message: string, isError: boolean?)
		ui.statusLabel.Text = message
		ui.statusLabel.TextColor3 = if isError
			then Color3.fromRGB(255, 120, 120)
			else Color3.fromRGB(170, 220, 170)
	end

	local function url(path: string): string
		return `http://127.0.0.1:{port}{path}`
	end

	local function request(method: string, path: string, body: string?, extraHeaders: { [string]: string }?): string
		local headers = {
			["X-Roblox-Sync-Token"] = token,
			["Content-Type"] = "application/json",
		}
		if extraHeaders then
			for key, value in extraHeaders do headers[key] = value end
		end
		local options = {
			Url = url(path),
			Method = method,
			Headers = headers,
			Timeout = 30,
		}
		if body ~= nil then
			options.Body = body
		end
		local response = nil
		local requestError = nil
		for attempt = 1, 3 do
			local ok, result = pcall(function()
				return HttpService:RequestAsync(options)
			end)
			if ok then
				response = result
				if response.Success or response.StatusCode < 500 then break end
				requestError = `HTTP {response.StatusCode}: {response.StatusMessage}`
			else
				requestError = tostring(result)
			end
			if attempt < 3 then task.wait(0.25 * attempt) end
		end
		if not response then
			error(`Local Blender request failed after 3 attempts: {requestError}`)
		end
		if not response.Success then
			error(`HTTP {response.StatusCode}: {response.StatusMessage} {response.Body}`)
		end
		return response.Body
	end

	local function fetchJson(path: string): any
		return HttpService:JSONDecode(request("GET", path))
	end

	local function fetchConnectionJson(candidatePort: number, candidateToken: string, path: string): any
		local headers = { ["Content-Type"] = "application/json" }
		if candidateToken ~= "" then
			headers["X-Roblox-Sync-Token"] = candidateToken
		end
		local requestOk, response = pcall(function()
			return HttpService:RequestAsync({
				Url = `http://127.0.0.1:{candidatePort}{path}`,
				Method = "GET",
				Headers = headers,
				Timeout = 1,
			})
		end)
		if not requestOk then
			error(`Port {candidatePort}: {tostring(response)}`)
		end
		if not response.Success then
			error(`Port {candidatePort}: HTTP {response.StatusCode} {response.StatusMessage}`)
		end
		return HttpService:JSONDecode(response.Body)
	end

	local function requestPairing(candidatePort: number): (number, string)
		local requestOk, response = pcall(function()
			return HttpService:RequestAsync({
				Url = `http://127.0.0.1:{candidatePort}/v1/pair`,
				Method = "POST",
				Headers = { ["Content-Type"] = "application/json" },
				Body = "{}",
				Timeout = 1,
			})
		end)
		if not requestOk then
			error(`Port {candidatePort}: {tostring(response)}`)
		end
		if not response.Success then
			if response.StatusCode == 403 then
				error("Blenderで Allow Studio Connection を押してください")
			end
			error(`Automatic pairing failed: HTTP {response.StatusCode} {response.StatusMessage}`)
		end
		local value = expectTable(HttpService:JSONDecode(response.Body), "pairing response")
		local pairedPort = expectNumber(value.port, "pairing.port")
		local pairedToken = expectString(value.token, "pairing.token")
		if pairedPort < 1024 or pairedPort > 65535
			or #pairedToken < 16 or not string.match(pairedToken, "^[0-9a-fA-F]+$") then
			error("Blender returned invalid pairing credentials")
		end
		return pairedPort, pairedToken
	end

	local function discoverPairing(preferredPort: number?): (number, string)
		local candidates = {}
		local seen = {}
		local function addCandidate(candidate: number?)
			if candidate and candidate >= 1024 and candidate <= 65535 and not seen[candidate] then
				seen[candidate] = true
				table.insert(candidates, candidate)
			end
		end
		addCandidate(preferredPort)
		-- The port shown in Blender is authoritative. Fall back to the default once,
		-- but do not probe a long range of closed ports during an interactive connect.
		addCandidate(DEFAULT_PORT)

		local lastError = "Blender Mesh Sync server was not found"
		local approvalRequired = false
		for _, candidate in candidates do
			local ok, pairedPort, pairedToken = pcall(requestPairing, candidate)
			if ok then return pairedPort, pairedToken end
			lastError = tostring(pairedPort)
			if string.find(lastError, "Allow Studio Connection", 1, true) then
				approvalRequired = true
			end
		end
		if approvalRequired then
			error("Blenderで Allow Studio Connection を押してください")
		end
		error(lastError)
	end

	local function fetchBlob(kind: string, record): string
		local byteSize = expectNumber(record.byteSize, `{kind}.byteSize`)
		local chunkCount = expectNumber(record.chunkCount, `{kind}.chunkCount`)
		if byteSize < 0 or byteSize > MAX_BLOB_BYTES or chunkCount < 1 or chunkCount > 10000 then
			error(`Invalid {kind} blob limits`)
		end
		local chunks = table.create(chunkCount)
		for index = 0, chunkCount - 1 do
			chunks[index + 1] = request("GET", `/v1/blob/{kind}/{record.hash}/{index}`)
		end
		local value = table.concat(chunks)
		if #value ~= byteSize then
			error(`{kind} blob size mismatch: expected {byteSize}, received {#value}`)
		end
		return value
	end

	local function cframeArray(value: CFrame): { number }
		return { value:GetComponents() }
	end

	local function ensureGuid(instance: Instance, attribute: string): string
		local value = instance:GetAttribute(attribute)
		if type(value) ~= "string" or value == "" then
			value = HttpService:GenerateGUID(false)
			instance:SetAttribute(attribute, value)
		end
		return value
	end

	local function imageContent(id: string): Content?
		if type(id) ~= "string" or id == "" then return nil end
		return Content.fromUri(id)
	end

	local function contentProperty(instance: Instance, contentName: string, legacyName: string?): Content?
		local ok, value = pcall(function() return (instance :: any)[contentName] end)
		if ok and typeof(value) == "Content" and value.SourceType ~= Enum.ContentSourceType.None then
			return value
		end
		if legacyName then
			local legacyOk, legacy = pcall(function() return (instance :: any)[legacyName] end)
			if legacyOk and type(legacy) == "string" then return imageContent(legacy) end
		end
		return nil
	end

	local function collectImage(content: Content?, role: string, images, imageBlobs): string?
		if content == nil then return nil end
		local ok, editableOrError = pcall(function()
			return AssetService:CreateEditableImageAsync(content)
		end)
		if not ok or not editableOrError then
			error(`Could not read {role} image. The logged-in Studio user or experience must own the asset or have explicit asset permission: {tostring(editableOrError)}`)
		end
		local editable = editableOrError
		local size = editable.Size
		if size.X < 1 or size.Y < 1 or size.X > 1024 or size.Y > 1024 then
			editable:Destroy()
			error(`{role} image is outside the 1024x1024 reverse-sync limit`)
		end
		local raw = buffer.tostring(editable:ReadPixelsBuffer(Vector2.zero, size))
		editable:Destroy()
		local hash = sha256(raw)
		if not images[hash] then
			images[hash] = {
				hash = hash,
				name = `Studio_{role}_{string.sub(hash, 1, 8)}`,
				role = role,
				width = size.X,
				height = size.Y,
				byteSize = #raw,
				chunkCount = math.max(1, math.ceil(#raw / CHUNK_BYTES)),
				sourceUri = if content.SourceType == Enum.ContentSourceType.Uri then content.Uri else nil,
			}
			imageBlobs[hash] = raw
		end
		return hash
	end

	local function appearanceFor(part: BasePart, appearances, images, imageBlobs): string
		local appearance = {
			mode = "MATERIAL",
			maps = {},
			material = part.Material.Name,
			color = { part.Color.R, part.Color.G, part.Color.B },
			transparency = part.Transparency,
		}
		local surface = part:FindFirstChildOfClass("SurfaceAppearance")
		if surface then
			appearance.mode = "TEXTURE"
			appearance.textureSource = "SURFACE_APPEARANCE"
			appearance.color = { surface.Color.R, surface.Color.G, surface.Color.B }
			appearance.maps.baseColor = collectImage(contentProperty(surface, "ColorMapContent", "ColorMap"), "baseColor", images, imageBlobs)
			appearance.maps.roughness = collectImage(contentProperty(surface, "RoughnessMapContent", "RoughnessMap"), "roughness", images, imageBlobs)
			appearance.maps.metallic = collectImage(contentProperty(surface, "MetalnessMapContent", "MetalnessMap"), "metallic", images, imageBlobs)
			appearance.maps.normal = collectImage(contentProperty(surface, "NormalMapContent", "NormalMap"), "normal", images, imageBlobs)
			appearance.maps.emissive = collectImage(contentProperty(surface, "EmissiveMaskContent", nil), "emissive", images, imageBlobs)
			appearance.emissiveStrength = surface.EmissiveStrength
			appearance.emissiveTint = { surface.EmissiveTint.R, surface.EmissiveTint.G, surface.EmissiveTint.B }
			appearance.alphaMode = surface.AlphaMode.Name
		elseif part:IsA("MeshPart") and part.TextureID ~= "" then
			appearance.mode = "TEXTURE"
			appearance.textureSource = "MESHPART_TEXTURE"
			appearance.maps.baseColor = collectImage(imageContent(part.TextureID), "baseColor", images, imageBlobs)
		end

		if part.MaterialVariant ~= "" then
			local variant = MaterialService:FindFirstChild(part.MaterialVariant)
			if variant and variant:IsA("MaterialVariant") and variant.BaseMaterial == part.Material then
				appearance.mode = "TEXTURE"
				appearance.textureSource = "MATERIAL_VARIANT"
				appearance.materialVariant = {
					name = variant.Name,
					baseMaterial = variant.BaseMaterial.Name,
					studsPerTile = variant.StudsPerTile,
					materialPattern = variant.MaterialPattern.Name,
					alphaMode = variant.AlphaMode.Name,
					emissiveStrength = variant.EmissiveStrength,
					emissiveTint = { variant.EmissiveTint.R, variant.EmissiveTint.G, variant.EmissiveTint.B },
				}
				appearance.maps.baseColor = collectImage(contentProperty(variant, "ColorMapContent", "ColorMap"), "baseColor", images, imageBlobs)
				appearance.maps.roughness = collectImage(contentProperty(variant, "RoughnessMapContent", "RoughnessMap"), "roughness", images, imageBlobs)
				appearance.maps.metallic = collectImage(contentProperty(variant, "MetalnessMapContent", "MetalnessMap"), "metallic", images, imageBlobs)
				appearance.maps.normal = collectImage(contentProperty(variant, "NormalMapContent", "NormalMap"), "normal", images, imageBlobs)
			end
		end
		local hash = sha256(HttpService:JSONEncode(appearance))
		appearance.hash = hash
		appearance.previewHash = hash
		appearances[hash] = appearance
		return hash
	end

	local function basicAppearanceFor(part: BasePart, appearances): string
		local appearance = {
			mode = "MATERIAL",
			maps = {},
			material = part.Material.Name,
			color = { part.Color.R, part.Color.G, part.Color.B },
			transparency = part.Transparency,
		}
		local hash = sha256(HttpService:JSONEncode(appearance))
		appearance.hash = hash
		appearance.previewHash = hash
		appearances[hash] = appearance
		return hash
	end

	local function serializeEditableMesh(part: BasePart, content: Content, meshes, meshBlobs): (string, Vector3)
		local ok, editableOrError = pcall(function()
			return AssetService:CreateEditableMeshAsync(content, { FixedSize = true })
		end)
		if not ok or not editableOrError then
			error(`{part:GetFullName()} のメッシュは所有権または共有権限の関係で読み取れません: {tostring(editableOrError)}`)
		end
		local editable = editableOrError
		local vertexIds = editable:GetVertices()
		local vertexIndex = {}
		local vertices = table.create(#vertexIds)
		for index, vertexId in vertexIds do
			vertexIndex[vertexId] = index - 1
			local position = editable:GetPosition(vertexId)
			vertices[index] = { position.X, position.Y, position.Z }
		end
		local hasUvs = #editable:GetUVs() > 0
		local hasColors = #editable:GetColors() > 0
		local triangles, normals, uvs, colors = {}, {}, {}, {}
		for _, faceId in editable:GetFaces() do
			local faceVertices = editable:GetFaceVertices(faceId)
			if #faceVertices >= 3 then
				for fan = 2, #faceVertices - 1 do
					local corners = { faceVertices[1], faceVertices[fan], faceVertices[fan + 1] }
					local triangle, faceNormals, faceUvs, faceColors = {}, {}, {}, {}
					for corner, vertexId in corners do
						triangle[corner] = vertexIndex[vertexId]
						local normalId = editable:GetVertexFaceNormal(vertexId, faceId)
						local normal = editable:GetNormal(normalId) or Vector3.yAxis
						faceNormals[corner] = { normal.X, normal.Y, normal.Z }
						if hasUvs then
							local uv = editable:GetUV(editable:GetVertexFaceUV(vertexId, faceId)) or Vector2.zero
							faceUvs[corner] = { uv.X, uv.Y }
						end
						if hasColors then
							local colorId = editable:GetVertexFaceColor(vertexId, faceId)
							local color = editable:GetColor(colorId) or Color3.new(1, 1, 1)
							faceColors[corner] = { color.R, color.G, color.B, editable:GetColorAlpha(colorId) or 1 }
						end
					end
					table.insert(triangles, triangle)
					table.insert(normals, faceNormals)
					if hasUvs then table.insert(uvs, faceUvs) end
					if hasColors then table.insert(colors, faceColors) end
				end
			end
		end
		local meshSize = editable:GetSize()
		editable:Destroy()
		if #triangles == 0 or #triangles > 20000 then error(`{part.Name} has an unsupported triangle count`) end
		local payload = {
			vertices = vertices,
			triangles = triangles,
			cornerNormals = normals,
			cornerUvs = uvs,
			cornerColors = colors,
		}
		local raw = HttpService:JSONEncode(payload)
		local hash = sha256(raw)
		if not meshes[hash] then
			local sourceUri = nil
			pcall(function()
				if content.SourceType == Enum.ContentSourceType.Uri then
					sourceUri = content.Uri
				end
			end)
			meshes[hash] = {
				hash = hash,
				name = part.Name,
				vertexCount = #vertices,
				triangleCount = #triangles,
				byteSize = #raw,
				chunkCount = math.max(1, math.ceil(#raw / CHUNK_BYTES)),
				sourceUri = sourceUri,
			}
			meshBlobs[hash] = raw
		end
		return hash, meshSize
	end

	local function partType(part: BasePart): string?
		if part:IsA("WedgePart") then return "Wedge" end
		if part:IsA("CornerWedgePart") then return "CornerWedge" end
		if part:IsA("Part") then
			if part.Shape == Enum.PartType.Ball then return "Ball" end
			if part.Shape == Enum.PartType.Cylinder then return "Cylinder" end
			return "Block"
		end
		return nil
	end

	local function collectStudioSelection(excludedBaseParts: { [Instance]: boolean }?, allowEmpty: boolean?)
		local roots = Selection:Get()
		if #roots == 0 then error("Select at least one Part, MeshPart, Model, or Folder") end
		local function generatedDocumentRoot(instance: Instance): Instance?
			local current: Instance? = instance
			local found = nil
			while current and current ~= workspace do
				if (current:IsA("Folder") or current:IsA("Model"))
					and current:GetAttribute("GeneratedByBlenderSync") == true
					and type(current:GetAttribute("BlenderModelId")) == "string" then
					-- Keep walking so malformed nested legacy roots resolve to the
					-- outermost file container rather than creating another document.
					found = current
				end
				current = current.Parent
			end
			return found
		end
		local function virtualDocumentMetadata(instance: Instance): (any, any, any)
			local current: Instance? = instance
			while current and current ~= workspace do
				local modelId = current:GetAttribute("BlenderDocumentModelId")
				if type(modelId) == "string" and modelId ~= "" then
					return modelId, current:GetAttribute("BlenderDocumentModelName"),
						current:GetAttribute("BlenderDocumentRootKind")
				end
				current = current.Parent
			end
			return nil, nil, nil
		end
		local generatedRoot = nil
		local documentModelId = nil
		local documentModelName = nil
		local documentRootKind = nil
		local hasUnscopedRoot = false
		for _, root in roots do
			local candidate = generatedDocumentRoot(root)
			local virtualModelId, virtualModelName, virtualRootKind = virtualDocumentMetadata(root)
			local candidateModelId = if candidate then candidate:GetAttribute("BlenderModelId")
				else virtualModelId
			local candidateModelName = if candidate then candidate.Name
				else virtualModelName
			local candidateRootKind = if candidate then candidate:GetAttribute("BlenderRootKind")
				else virtualRootKind
			if candidate then
				if generatedRoot and generatedRoot ~= candidate then
					error("Selection spans multiple Blender sync files; send each file separately")
				end
				generatedRoot = candidate
			end
			if type(candidateModelId) == "string" and candidateModelId ~= "" then
				if documentModelId and documentModelId ~= candidateModelId then
					error("Selection spans multiple Blender sync files; send each file separately")
				end
				documentModelId = candidateModelId
				if type(candidateModelName) == "string" and candidateModelName ~= "" then
					documentModelName = candidateModelName
				end
				if type(candidateRootKind) == "string" and candidateRootKind ~= "" then
					documentRootKind = candidateRootKind
				end
			else
				hasUnscopedRoot = true
			end
		end
		if documentModelId and hasUnscopedRoot then
			error("Selection mixes Blender-synced and unrelated objects; send them separately")
		end
		local modelRoot = generatedRoot or (if not documentModelId and #roots == 1
			and roots[1]:IsA("Model") then roots[1] else nil)
		local included, hierarchyInstances, parts = {}, {}, {}
		local function includeHierarchyOnly(instance: Instance)
			if included[instance] then return end
			included[instance] = true
			table.insert(hierarchyInstances, instance)
		end
		local function visit(instance: Instance, recurseDescendants: boolean?)
			if included[instance] then return end
			included[instance] = true
			local isContainer = instance:IsA("Model") or instance:IsA("Folder")
			if isContainer then
				table.insert(hierarchyInstances, instance)
			elseif instance:IsA("BasePart") and not (excludedBaseParts and excludedBaseParts[instance]) then
				table.insert(parts, instance)
			end
			-- A selected Model/Folder can contain exportable geometry below
			-- Configuration, Accessory, BasePart, or other intermediate instances.
			-- Walk every descendant and only emit supported hierarchy/geometry types.
			if recurseDescendants == true or isContainer then
				for _, child in instance:GetChildren() do visit(child, true) end
			end
		end
		for _, root in roots do
			-- The generated file Folder is the document boundary, not a Blender
			-- Collection. Export its children so a round trip does not create an
			-- extra Collection with the .blend filename.
			if root == generatedRoot then
				for _, child in root:GetChildren() do visit(child, true) end
			else
				visit(root, false)
			end
		end
		-- A Part-only selection still needs its generated ancestors so Blender
		-- can reconstruct Studio hierarchy for new objects. Do not recurse into
		-- siblings while adding these ancestor records.
		for _, root in roots do
			local ancestor = root.Parent
			while ancestor and ancestor ~= generatedRoot and ancestor ~= workspace do
				if ancestor:IsA("Model") or ancestor:IsA("Folder") then
					includeHierarchyOnly(ancestor)
				end
				ancestor = ancestor.Parent
			end
		end
		if #parts == 0 and not allowEmpty then error("Selection contains no supported Parts or MeshParts") end

		local hierarchyIds, seenHierarchyIds = {}, {}
		for _, instance in hierarchyInstances do
			local id = ensureGuid(instance, "BlenderHierarchyId")
			if seenHierarchyIds[id] then
				id = HttpService:GenerateGUID(false)
				instance:SetAttribute("BlenderHierarchyId", id)
			end
			seenHierarchyIds[id] = true
			hierarchyIds[instance] = id
		end
		local function hierarchyKind(instance: Instance): string
			local storedKind = instance:GetAttribute("BlenderHierarchyKind")
			if storedKind == "MODEL" or storedKind == "FOLDER" then return storedKind end
			return if instance:IsA("Model") then "MODEL" else "FOLDER"
		end
		local hierarchy = table.create(#hierarchyInstances)
		for _, instance in hierarchyInstances do
			local parent = instance.Parent
			while parent and not hierarchyIds[parent] do parent = parent.Parent end
			local parentId = if parent then hierarchyIds[parent] else nil
			local kind = hierarchyKind(instance)
			local node = {
				id = hierarchyIds[instance],
				name = instance.Name,
				kind = kind,
				parentId = parentId,
			}
			if kind == "MODEL" then
				local emptyCFrame = if instance:IsA("Model")
					then instance:GetPivot() else instance:GetAttribute("BlenderEmptyCFrame")
				if typeof(emptyCFrame) == "CFrame" then node.cframe = cframeArray(emptyCFrame) end
				local collection = instance.Parent
				while collection and (not hierarchyIds[collection] or hierarchyKind(collection) ~= "FOLDER") do
					collection = collection.Parent
				end
				if collection then node.primaryCollectionId = hierarchyIds[collection] end
			end
			table.insert(hierarchy, node)
		end

		local meshes, meshBlobs, images, imageBlobs, appearances = {}, {}, {}, {}, {}
		local skippedObjects, skippedGroups, unreadableMeshSources = {}, {}, {}
		local propertyOnlyWarnings = {}
		local exportRootKind = if modelRoot
			then modelRoot:GetAttribute("BlenderRootKind") or "STUDIO_SELECTION"
			else documentRootKind or "STUDIO_SELECTION"
		local function mergeRecords(target, staged)
			for key, value in staged do
				if target[key] == nil then target[key] = value end
			end
		end
		local function meshSource(part: MeshPart): (string, string?)
			local source = if part.MeshContent.SourceType == Enum.ContentSourceType.Uri
				then part.MeshContent.Uri else part.MeshId
			local assetId = string.match(source, "%d+")
			return source, assetId
		end
		local function recordSkip(part: BasePart, category: string, reason: string, source: string?, assetId: string?)
			local skipped = {
				name = part.Name,
				path = part:GetFullName(),
				className = part.ClassName,
				category = category,
				source = source,
				assetId = assetId,
				error = reason,
			}
			table.insert(skippedObjects, skipped)
			local key = `{category}:{assetId or source or part.ClassName}`
			local group = skippedGroups[key]
			if group then
				group.count += 1
			else
				skippedGroups[key] = {
					count = 1,
					category = category,
					path = skipped.path,
					assetId = assetId,
					error = reason,
				}
			end
		end
		local objects = table.create(#parts)
		local seenObjectIds = {}
		for _, part in parts do
			local parent = part.Parent
			while parent and (not hierarchyIds[parent] or hierarchyKind(parent) ~= "MODEL") do
				parent = parent.Parent
			end
			local parentId = if parent then hierarchyIds[parent] else nil
			local collection = part.Parent
			while collection and (not hierarchyIds[collection] or hierarchyKind(collection) ~= "FOLDER") do
				collection = collection.Parent
			end
			local primaryCollectionId = if collection then hierarchyIds[collection] else nil
			local collectionIds = if primaryCollectionId then { primaryCollectionId } else {}
			local originalCollectionIds = {}
			local storedCollections = part:GetAttribute("BlenderCollectionIds")
			if type(storedCollections) == "string" and storedCollections ~= "" then
				local decodedOk, decoded = pcall(HttpService.JSONDecode, HttpService, storedCollections)
				if decodedOk and type(decoded) == "table" then originalCollectionIds = decoded end
			end
			local objectId = ensureGuid(part, "BlenderObjectId")
			if seenObjectIds[objectId] then
				objectId = HttpService:GenerateGUID(false)
				part:SetAttribute("BlenderObjectId", objectId)
			end
			seenObjectIds[objectId] = true
			local record = {
				id = objectId,
				name = part.Name,
				size = { part.Size.X, part.Size.Y, part.Size.Z },
				cframe = cframeArray(part.CFrame),
				material = part.Material.Name,
				color = { part.Color.R, part.Color.G, part.Color.B },
				transparency = part.Transparency,
				anchored = part.Anchored,
				canCollide = part.CanCollide,
				canTouch = part.CanTouch,
				canQuery = part.CanQuery,
				castShadow = part.CastShadow,
				parentId = parentId,
				primaryCollectionId = primaryCollectionId,
				collectionIds = collectionIds,
				originalParentId = part:GetAttribute("BlenderOriginalParentId"),
				originalCollectionIds = originalCollectionIds,
			}
			local storedOwner = part:GetAttribute("BlenderGeometryOwner")
			local geometryOwner = "STUDIO"
			if storedOwner == "BLENDER" or storedOwner == "STUDIO" then
				geometryOwner = storedOwner
			elseif exportRootKind == "BLENDER_SCENE"
				and part:GetAttribute("GeneratedByBlenderSync") == true then
				geometryOwner = "BLENDER"
			end
			record.geometryOwner = geometryOwner
			if part:IsA("MeshPart") then
				-- Stage every asset produced by this MeshPart. If mesh or image
				-- permissions reject it, discard only this staging area so readable
				-- Parts and MeshParts can still be transferred atomically.
				local source, assetId = meshSource(part)
				local storedAsset = part:GetAttribute("RobloxMeshAssetId")
				local sourceAssetId = if storedAsset ~= nil then string.match(tostring(storedAsset), "%d+") else nil
				record.sourceMeshAssetId = sourceAssetId
				record.currentMeshAssetId = assetId
				record.geometryChanged = geometryOwner == "BLENDER" and sourceAssetId ~= nil
					and assetId ~= nil and sourceAssetId ~= assetId
				local cachedError = unreadableMeshSources[source]
				if cachedError then
					error(`{part:GetFullName()} のMeshPartは所有権または共有権限の関係で読み取れないため、Blenderへ送信しません: {cachedError}`)
				end
				local stagedMeshes, stagedMeshBlobs = {}, {}
				local stagedImages, stagedImageBlobs, stagedAppearances = {}, {}, {}
				local readable, meshHashOrError, meshSize
				if cachedError then
					readable, meshHashOrError, meshSize = false, cachedError, nil
				else
					readable, meshHashOrError, meshSize = pcall(function()
						return serializeEditableMesh(part, part.MeshContent, stagedMeshes, stagedMeshBlobs)
					end)
				end
				local appearanceReadable, appearanceHashOrError = pcall(function()
					return appearanceFor(part, stagedAppearances, stagedImages, stagedImageBlobs)
				end)
				if not appearanceReadable then
					local appearanceError = tostring(appearanceHashOrError)
					stagedImages, stagedImageBlobs, stagedAppearances = {}, {}, {}
					appearanceHashOrError = basicAppearanceFor(part, stagedAppearances)
					record.appearanceAvailable = false
					record.appearanceError = appearanceError
					table.insert(propertyOnlyWarnings,
						`{part:GetFullName()}: PBR images unavailable; geometry and properties sent without replacing the original Studio appearance. Reason: {appearanceError}`)
				else
					record.appearanceAvailable = true
				end
				if not readable then
					local reason = tostring(meshHashOrError)
					unreadableMeshSources[source] = reason
					error(`{part:GetFullName()} のMeshPartは所有権または共有権限の関係で読み取れないため、Blenderへ送信しません: {reason}`)
				end
				if readable then
					mergeRecords(meshes, stagedMeshes)
					mergeRecords(meshBlobs, stagedMeshBlobs)
					record.meshHash = meshHashOrError
					record.meshSize = { meshSize.X, meshSize.Y, meshSize.Z }
					record.geometryAvailable = true
				else
					record.geometryAvailable = false
					unreadableMeshSources[source] = tostring(meshHashOrError)
					table.insert(propertyOnlyWarnings, `{part:GetFullName()}: mesh unavailable; properties only`)
				end
				mergeRecords(images, stagedImages)
				mergeRecords(imageBlobs, stagedImageBlobs)
				mergeRecords(appearances, stagedAppearances)
				record.kind = "MESH"
				record.collisionFidelity = part.CollisionFidelity.Name
				record.appearanceHash = appearanceHashOrError
			else
				record.kind = "PART"
				record.partType = partType(part)
				if not record.partType then
					recordSkip(
						part,
						"Unsupported BasePart",
						`{part.ClassName} geometry is not exposed by the Studio EditableMesh API`,
						nil,
						nil
					)
					continue
				end
				local stagedImages, stagedImageBlobs, stagedAppearances = {}, {}, {}
				local appearanceReadable, appearanceHashOrError = pcall(function()
					return appearanceFor(part, stagedAppearances, stagedImages, stagedImageBlobs)
				end)
				if not appearanceReadable then
					local appearanceError = tostring(appearanceHashOrError)
					stagedImages, stagedImageBlobs, stagedAppearances = {}, {}, {}
					appearanceHashOrError = basicAppearanceFor(part, stagedAppearances)
					record.appearanceAvailable = false
					record.appearanceError = appearanceError
					table.insert(propertyOnlyWarnings,
						`{part:GetFullName()}: PBR images unavailable; Part geometry and properties sent without replacing the original Studio appearance. Reason: {appearanceError}`)
				else
					record.appearanceAvailable = true
				end
				mergeRecords(images, stagedImages)
				mergeRecords(imageBlobs, stagedImageBlobs)
				mergeRecords(appearances, stagedAppearances)
				record.appearanceHash = appearanceHashOrError
			end
			record.stateHash = sha256(HttpService:JSONEncode(record))
			table.insert(objects, record)
		end
		for _, group in skippedGroups do
			warn(`[Blender Mesh Sync] Skipped {group.count} {group.category}: {group.path}`
				.. (if group.assetId then ` (Asset ID {group.assetId})` else "")
				.. `. Reason: {group.error}`)
		end
		if #objects == 0 and not allowEmpty then
			if #skippedObjects > 0 then
				local first = skippedObjects[1]
				error(`All {#skippedObjects} selected objects were unreadable or unsupported. First: {first.path}`
					.. (if first.assetId then ` (Asset ID {first.assetId})` else ""))
			end
			error("Selection contains no supported readable Parts or MeshParts")
		end

		local modelId = if modelRoot then ensureGuid(modelRoot, "BlenderModelId")
			else documentModelId or plugin:GetSetting("RobloxMeshSync.ReverseModelId.v1")
		if type(modelId) ~= "string" or modelId == "" then
			modelId = HttpService:GenerateGUID(false)
			plugin:SetSetting("RobloxMeshSync.ReverseModelId.v1", modelId)
		end
		local function values(dictionary)
			local result = {}
			for _, value in dictionary do table.insert(result, value) end
			return result
		end
		return {
			schema = REVERSE_SCHEMA_ID,
			generator = { name = "Roblox Primitive Sync", version = PLUGIN_VERSION },
			model = {
				id = modelId,
				name = if modelRoot then modelRoot.Name else documentModelName or "Studio Selection",
				rootKind = if modelRoot then modelRoot:GetAttribute("BlenderRootKind") or "STUDIO_SELECTION"
					else documentRootKind or "STUDIO_SELECTION",
			},
			transformMask = {
				position = ui.positionToggle:GetAttribute("ChannelEnabled") ~= false,
				rotation = ui.rotationToggle:GetAttribute("ChannelEnabled") ~= false,
				scale = ui.scaleToggle:GetAttribute("ChannelEnabled") ~= false,
			},
			hierarchy = hierarchy,
			objects = objects,
			meshes = values(meshes),
			images = values(images),
			appearances = values(appearances),
		}, meshBlobs, imageBlobs, skippedObjects, propertyOnlyWarnings, hierarchyIds
	end

	local function sendSelectionToBlender()
		if not connected then setStatus("Connect to Blender first", true) return end
		local ok, message = xpcall(function()
			setStatus("Reading Studio selection...", false)
			local document, meshBlobs, imageBlobs, skippedObjects, propertyOnlyWarnings = collectStudioSelection()
			local transferId = HttpService:GenerateGUID(false)
			request("POST", "/v2/reverse/begin", HttpService:JSONEncode({ transferId = transferId, document = document }))
			for kind, blobs in { mesh = meshBlobs, image = imageBlobs } do
				for hash, raw in blobs do
					local chunkCount = math.max(1, math.ceil(#raw / CHUNK_BYTES))
					for index = 0, chunkCount - 1 do
						local first = index * CHUNK_BYTES + 1
						local chunk = string.sub(raw, first, math.min(#raw, first + CHUNK_BYTES - 1))
						request("POST", `/v2/reverse/blob/{kind}/{hash}/{index}`, chunk, {
							["Content-Type"] = "application/octet-stream",
							["X-Roblox-Sync-Transfer"] = transferId,
						})
					end
				end
			end
			local result = HttpService:JSONDecode(request("POST", "/v2/reverse/commit", HttpService:JSONEncode({ transferId = transferId })))
			for _, warningText in propertyOnlyWarnings do
				warn(`[Blender Mesh Sync] {warningText}`)
			end
			if #skippedObjects > 0 then
				warn(`[Blender Mesh Sync] Sent {#document.objects} objects and skipped {#skippedObjects} unreadable or unsupported objects.`)
				return `Sent {#document.objects} objects; skipped {#skippedObjects}; property-only {#propertyOnlyWarnings}. Outputに詳細を表示しました。`
			end
			if #propertyOnlyWarnings > 0 then
				return `Sent {#document.objects} objects; {#propertyOnlyWarnings} used property-only fallback. Outputに詳細を表示しました。`
			end
			return `Sent {#document.objects} objects to Blender as incoming revision {result.revision}. Review and Apply in Blender.`
		end, debug.traceback)
		if ok then setStatus(message, false) else
			warn("Studio to Blender Mesh Sync failed:\n" .. tostring(message))
			if string.find(tostring(message), "所有権または共有権限") then
				setStatus("メッシュを所有権または共有権限の関係で送れません。Blenderには送信していません。", true)
			else
				setStatus("Export failed. Outputを確認してください。", true)
			end
		end
	end

	local function sendCsgSelectionToBlender(selectedOperations: { Instance }?, ordinaryTransfer: any?)
		local selectionBefore = Selection:Get()
		local operations = selectedOperations or {}
		if not selectedOperations then
			for _, instance in selectionBefore do
				if instance:IsA("UnionOperation") or instance:IsA("IntersectOperation") then
					table.insert(operations, instance)
				end
			end
		end
		if #operations == 0 then
			setStatus("UnionまたはIntersectを直接選択してください。", true)
			return
		end
		local hierarchyIds = if ordinaryTransfer then ordinaryTransfer.hierarchyIds else nil
		local function operationHierarchy(original: Instance)
			local modelParent = original.Parent
			while modelParent and (not hierarchyIds or not hierarchyIds[modelParent] or not modelParent:IsA("Model")) do
				modelParent = modelParent.Parent
			end
			local folderParent = original.Parent
			while folderParent and (not hierarchyIds or not hierarchyIds[folderParent] or not folderParent:IsA("Folder")) do
				folderParent = folderParent.Parent
			end
			local parentId = if modelParent and hierarchyIds then hierarchyIds[modelParent] else nil
			local primaryCollectionId = if folderParent and hierarchyIds then hierarchyIds[folderParent] else nil
			return parentId, primaryCollectionId,
				if primaryCollectionId then { primaryCollectionId } else {}
		end

		local recording = nil
		local temporaryFolder = nil
		local stats = {
			separatePassed = 0,
			negatePassed = 0,
			failures = 0,
			nodes = 0,
		}
		local phase1Document = {
			schema = "roblox-mesh-sync-csg-phase1/1",
			generator = { name = "Roblox Primitive Sync", version = PLUGIN_VERSION },
			roots = {},
			instances = {},
			csg = {},
			meshes = {},
			meshBlobs = {},
		}
		local sourceGuids = {}
		for _, operation in operations do
			local sourceGuid = operation:GetAttribute("BlenderObjectId")
			if type(sourceGuid) ~= "string" or sourceGuid == "" then
				sourceGuid = HttpService:GenerateGUID(false)
			end
			sourceGuids[operation] = sourceGuid
		end
		local function pass(message: string)
			print(`[Blender Mesh Sync][Union Phase 1][PASS] {message}`)
		end
		local function fail(message: string)
			stats.failures += 1
			stats.lastFailure = message
			warn(`[Blender Mesh Sync][Union Phase 1][FAIL] {message}`)
		end
		local function propertyText(instance: Instance, propertyName: string): string
			local readable, value = pcall(function()
				return (instance :: any)[propertyName]
			end)
			if readable then return tostring(value) end
			return `<unavailable: {value}>`
		end
		local function describe(instance: Instance, prefix: string)
			print(`[Blender Mesh Sync][Union Phase 1] {prefix}`
				.. ` ClassName={instance.ClassName}`
				.. ` Name={instance.Name}`
				.. ` Shape={propertyText(instance, "Shape")}`
				.. ` Size={propertyText(instance, "Size")}`
				.. ` CFrame={propertyText(instance, "CFrame")}`
				.. ` TriangleCount={propertyText(instance, "TriangleCount")}`)
			for _, child in instance:GetChildren() do
				if child:IsA("DataModelMesh") then
					print(`[Blender Mesh Sync][Union Phase 1] {prefix}`
						.. ` DataModelMesh={child.ClassName}`
						.. ` Scale={propertyText(child, "Scale")}`
						.. ` Offset={propertyText(child, "Offset")}`)
				end
			end
		end
		local function pluginResults(value: any, operationName: string): { Instance }?
			if typeof(value) == "Instance" then return { value } end
			if type(value) ~= "table" then
				fail(`plugin:{operationName} returned {typeof(value)}, not an Instance array`)
				return nil
			end
			local instances = {}
			for index, item in value do
				if typeof(item) ~= "Instance" then
					fail(`plugin:{operationName} result {index} is {typeof(item)}, not an Instance`)
				else
					table.insert(instances, item)
				end
			end
			return instances
		end
		local function addLeaf(instance: Instance): any?
			if not instance:IsA("BasePart") then
				fail(`separated operand {instance.Name} is {instance.ClassName}, not a BasePart`)
				return nil
			end
			local part = instance :: BasePart
			local dataModelMesh = part:FindFirstChildWhichIsA("DataModelMesh")
			local meshData = if dataModelMesh then dataModelMesh :: any else nil
			local specialMeshType = nil
			local isExternalSpecialMesh = false
			if dataModelMesh and dataModelMesh:IsA("SpecialMesh") then
				specialMeshType = meshData.MeshType.Name
				isExternalSpecialMesh = meshData.MeshType == Enum.MeshType.FileMesh
			elseif dataModelMesh and dataModelMesh:IsA("FileMesh") then
				specialMeshType = "FileMesh"
				isExternalSpecialMesh = true
			end
			local kind = if part:IsA("MeshPart") or isExternalSpecialMesh then "MESH" else "PART"
			local primitiveType = if kind == "PART" then partType(part) else nil
			if dataModelMesh then
				if dataModelMesh:IsA("BlockMesh") then
					primitiveType = "Block"
				elseif dataModelMesh:IsA("CylinderMesh") then
					primitiveType = "Cylinder"
				elseif specialMeshType then
					local builtInTypes = {
						Brick = "Block",
						Sphere = "Ball",
						Cylinder = "Cylinder",
						Wedge = "Wedge",
						CornerWedge = "CornerWedge",
					}
					if specialMeshType ~= "FileMesh" then
						primitiveType = builtInTypes[specialMeshType]
						if not primitiveType then
							fail(`SpecialMesh {part:GetFullName()} のMeshType={specialMeshType}は現在正確に再現できないため、Blenderへ送信しません。`)
							return nil
						end
					end
				end
			end
			if kind == "PART" and not primitiveType then
				fail(`separated operand {part.Name} has unsupported class {part.ClassName}`)
				return nil
			end
			local id = HttpService:GenerateGUID(false)
			local record = {
				id = id,
				name = part.Name,
				kind = kind,
				className = part.ClassName,
				partType = primitiveType,
				size = { part.Size.X, part.Size.Y, part.Size.Z },
				cframe = cframeArray(part.CFrame),
				material = part.Material.Name,
				color = { part.Color.R, part.Color.G, part.Color.B },
				transparency = part.Transparency,
				anchored = part.Anchored,
				canCollide = part.CanCollide,
				canTouch = part.CanTouch,
				canQuery = part.CanQuery,
				castShadow = part.CastShadow,
				geometryOwner = "STUDIO",
				geometryAvailable = kind == "PART",
			}
			if dataModelMesh then
				record.dataModelMeshClass = dataModelMesh.ClassName
				record.specialMeshType = specialMeshType
				record.meshScale = {
					meshData.Scale.X,
					meshData.Scale.Y,
					meshData.Scale.Z,
				}
				record.meshOffset = {
					meshData.Offset.X,
					meshData.Offset.Y,
					meshData.Offset.Z,
				}
			end
			if kind == "MESH" then
				local content = nil
				if part:IsA("MeshPart") then
					content = (part :: MeshPart).MeshContent
				elseif meshData then
					local contentReadable, meshContent = pcall(function()
						return meshData.MeshContent
					end)
					if contentReadable then content = meshContent end
					local hasMeshContent = false
					if content then
						pcall(function()
							hasMeshContent = content.SourceType ~= Enum.ContentSourceType.None
						end)
					end
					if not hasMeshContent then
						local meshId = tostring(meshData.MeshId)
						if meshId ~= "" then content = Content.fromUri(meshId) end
					end
				end
				local readable, meshHashOrError, meshSize = pcall(function()
					if not content then error("mesh content is empty") end
					return serializeEditableMesh(
						part,
						content,
						phase1Document.meshes,
						phase1Document.meshBlobs
					)
				end)
				if not readable then
					fail(`{part:GetFullName()} のメッシュは所有権または共有権限の関係で読み取れないため、Blenderへ送信しません: {tostring(meshHashOrError)}`)
					return nil
				end
				record.meshHash = meshHashOrError
				record.meshSize = { meshSize.X, meshSize.Y, meshSize.Z }
				record.geometryAvailable = true
				if isExternalSpecialMesh then
					record.dataModelMeshAbsoluteScale = true
				end
			end
			table.insert(phase1Document.instances, record)
			return { kind = "instance", ref = id }
		end

		local inspectOperation
		inspectOperation = function(operation: Instance, depth: number, displayName: string?): any?
			stats.nodes += 1
			if depth > 32 or stats.nodes > 256 then
				fail("CSG nesting or operand count exceeded the Phase 1 safety limit")
				return nil
			end
			local nodeId = HttpService:GenerateGUID(false)
			local usePartColor = nil
			pcall(function() usePartColor = (operation :: any).UsePartColor end)
			local operationPart = operation :: BasePart
			local node = {
				id = nodeId,
				name = displayName or operation.Name,
				className = operation.ClassName,
				op = if operation:IsA("IntersectOperation") then "intersect" else "union",
				sourceGuid = operation:GetAttribute("BlenderObjectId"),
				size = { operationPart.Size.X, operationPart.Size.Y, operationPart.Size.Z },
				cframe = cframeArray(operationPart.CFrame),
				appearance = {
					material = operationPart.Material.Name,
					color = { operationPart.Color.R, operationPart.Color.G, operationPart.Color.B },
					transparency = operationPart.Transparency,
				},
				usePartColor = usePartColor,
				physics = {
					anchored = operationPart.Anchored,
					canCollide = operationPart.CanCollide,
					canTouch = operationPart.CanTouch,
					canQuery = operationPart.CanQuery,
					castShadow = operationPart.CastShadow,
				},
				operands = {},
			}
			table.insert(phase1Document.csg, node)
			describe(operation, `depth={depth} operation before Separate:`)
			local separated, rawChildren = pcall(function()
				return plugin:Separate({ operation })
			end)
			if not separated then
				fail(`plugin:Separate failed at depth {depth}: {rawChildren}`)
				return nil
			end
			local children = pluginResults(rawChildren, "Separate")
			if not children then return nil end
			stats.separatePassed += 1
			pass(`plugin:Separate returned {#children} operand(s) at depth {depth}`)
			for index, child in children do
				describe(child, `depth={depth} operand={index} after Separate:`)
				if child:IsA("NegateOperation") then
					local negated, rawRestored = pcall(function()
						return plugin:Negate({ child })
					end)
					if not negated then
						fail(`plugin:Negate failed for negative operand {index}: {rawRestored}`)
						continue
					end
					local restored = pluginResults(rawRestored, "Negate")
					if not restored then continue end
					local restoredToSupportedOperand = false
					for restoredIndex, restoredPart in restored do
						describe(restoredPart, `depth={depth} negative={index} restored={restoredIndex}:`)
						if restoredPart:IsA("UnionOperation") or restoredPart:IsA("IntersectOperation") then
							local csgReference = inspectOperation(restoredPart, depth + 1, nil)
							if csgReference then
								csgReference.role = "negative"
								table.insert(node.operands, csgReference)
								restoredToSupportedOperand = true
							end
						else
							local leafReference = addLeaf(restoredPart)
							if leafReference then
								leafReference.role = "negative"
								table.insert(node.operands, leafReference)
								restoredToSupportedOperand = true
							end
						end
					end
					if restoredToSupportedOperand then
						stats.negatePassed += 1
						pass(`plugin:Negate restored negative operand {index} to a supported Part or nested CSG operation`)
					else
						fail(`plugin:Negate did not restore negative operand {index} to a supported Part or nested CSG operation`)
					end
				elseif child:IsA("UnionOperation") or child:IsA("IntersectOperation") then
					pass(`nested {child.ClassName} remained intact after separating its parent`)
					local childReference = inspectOperation(child, depth + 1, nil)
					if childReference then
						childReference.role = "positive"
						table.insert(node.operands, childReference)
					end
				else
					local leafReference = addLeaf(child)
					if leafReference then
						leafReference.role = "positive"
						table.insert(node.operands, leafReference)
					end
				end
			end
			return { kind = "csg", ref = nodeId }
		end

		local completed, testError = xpcall(function()
			recording = ChangeHistoryService:TryBeginRecording("Blender CSG Phase 1 Tree Build")
			if not recording then
				error("Could not start ChangeHistoryService recording; finish the current Studio action and retry")
			end
			temporaryFolder = Instance.new("Folder")
			temporaryFolder.Name = `__BlenderMeshSync_CSG_Phase1_{HttpService:GenerateGUID(false)}`
			temporaryFolder.Parent = workspace
			print("[Blender Mesh Sync][Union Phase 1] ===== TREE BUILD START =====")
			print(`[Blender Mesh Sync][Union Phase 1] Building {#operations} selected operation tree(s) on clones only.`)
			for index, original in operations do
				local originalParent = original.Parent
				local originalClassName = original.ClassName
				local originalSize = original.Size
				local originalCFrame = original.CFrame
				describe(original, `source={index}:`)
				local clone = original:Clone()
				if not clone then error(`Could not clone {original:GetFullName()}`) end
				clone:SetAttribute("BlenderObjectId", sourceGuids[original])
				clone.Name = `Phase1Clone_{index}_{original.Name}`
				clone.Parent = temporaryFolder
				local rootReference = inspectOperation(clone, 0, original.Name)
				if rootReference then
					local parentId, primaryCollectionId, collectionIds = operationHierarchy(original)
					table.insert(phase1Document.roots, {
						name = original.Name,
						sourceGuid = sourceGuids[original],
						sourcePath = original:GetFullName(),
						parentId = parentId,
						primaryCollectionId = primaryCollectionId,
						collectionIds = collectionIds,
						kind = rootReference.kind,
						ref = rootReference.ref,
					})
				end
				if original.Parent == originalParent
					and original.ClassName == originalClassName
					and original.Size == originalSize
					and original.CFrame == originalCFrame then
					pass(`source {index} remained unchanged`)
				else
					fail(`source {index} changed unexpectedly`)
				end
			end
		end, debug.traceback)

		if temporaryFolder then
			local cleaned, cleanupError = pcall(function() temporaryFolder:Destroy() end)
			if cleaned then pass("temporary Workspace folder was destroyed")
			else fail(`temporary folder cleanup failed: {cleanupError}`) end
		end
		local selectionRestored, selectionError = pcall(function() Selection:Set(selectionBefore) end)
		if not selectionRestored then fail(`selection restore failed: {selectionError}`) end
		if recording then
			local cancelled, cancelError = pcall(function()
				ChangeHistoryService:FinishRecording(recording, Enum.FinishRecordingOperation.Cancel)
			end)
			if cancelled then pass("ChangeHistoryService recording was cancelled")
			else fail(`ChangeHistoryService cancel failed: {cancelError}`) end
		end
		print(`[Blender Mesh Sync][Union Phase 1] ===== TREE BUILD END: CSG={#phase1Document.csg}, LEAVES={#phase1Document.instances}, Separate PASS={stats.separatePassed}, Negate PASS={stats.negatePassed}, FAIL={stats.failures} =====`)
		if not completed then
			warn("[Blender Mesh Sync][Union Phase 1] Tree build aborted:\n" .. tostring(testError))
			setStatus("Union Phase 1を中断しました。Outputを確認してください。", true)
		elseif stats.failures > 0 then
			if stats.lastFailure and string.find(stats.lastFailure, "所有権または共有権限") then
				setStatus("メッシュを所有権または共有権限の関係で送れません。Blenderには送信していません。", true)
			else
				setStatus(`Union Phase 1完了（失敗 {stats.failures}件）。Outputを確認してください。`, true)
			end
		else
			local function values(dictionary)
				local items = {}
				for _, value in dictionary do table.insert(items, value) end
				return items
			end
			local modelId = plugin:GetSetting("RobloxMeshSync.CsgReverseModelId.v1")
			if type(modelId) ~= "string" or modelId == "" then
				modelId = HttpService:GenerateGUID(false)
				plugin:SetSetting("RobloxMeshSync.CsgReverseModelId.v1", modelId)
			end
			local ordinaryDocument = if ordinaryTransfer then ordinaryTransfer.document else nil
			local combinedObjects = {}
			if ordinaryDocument then
				for _, record in ordinaryDocument.objects do table.insert(combinedObjects, record) end
			end
			for _, record in phase1Document.instances do table.insert(combinedObjects, record) end
			local combinedMeshes = {}
			if ordinaryDocument then
				for _, record in ordinaryDocument.meshes do table.insert(combinedMeshes, record) end
			end
			for _, record in values(phase1Document.meshes) do table.insert(combinedMeshes, record) end
			local reverseDocument = {
				schema = CSG_REVERSE_SCHEMA_ID,
				generator = phase1Document.generator,
				model = if ordinaryDocument then ordinaryDocument.model else {
					id = modelId,
					name = if #operations == 1 then operations[1].Name else "Studio CSG Selection",
					rootKind = "STUDIO_SELECTION",
				},
				transformMask = if ordinaryDocument then ordinaryDocument.transformMask else {
					position = ui.positionToggle:GetAttribute("ChannelEnabled") ~= false,
					rotation = ui.rotationToggle:GetAttribute("ChannelEnabled") ~= false,
					scale = ui.scaleToggle:GetAttribute("ChannelEnabled") ~= false,
				},
				hierarchy = if ordinaryDocument then ordinaryDocument.hierarchy else {},
				objects = combinedObjects,
				meshes = combinedMeshes,
				images = if ordinaryDocument then ordinaryDocument.images else {},
				appearances = if ordinaryDocument then ordinaryDocument.appearances else {},
				csg = phase1Document.csg,
				csgRoots = phase1Document.roots,
				csgPhase = 4,
			}
			print("[Blender Mesh Sync][Union Phase 4] ===== REVERSE/4 JSON =====")
			print(HttpService:JSONEncode(reverseDocument))
			print("[Blender Mesh Sync][Union Phase 4] ===== REVERSE/4 JSON END =====")
			local sent, sendResult = xpcall(function()
				local transferId = HttpService:GenerateGUID(false)
				request("POST", "/v2/reverse/begin", HttpService:JSONEncode({
					transferId = transferId,
					document = reverseDocument,
				}))
				local combinedMeshBlobs = {}
				if ordinaryTransfer then
					for hash, raw in ordinaryTransfer.meshBlobs do combinedMeshBlobs[hash] = raw end
				end
				for hash, raw in phase1Document.meshBlobs do combinedMeshBlobs[hash] = raw end
				for hash, raw in combinedMeshBlobs do
					local chunkCount = math.max(1, math.ceil(#raw / CHUNK_BYTES))
					for index = 0, chunkCount - 1 do
						local first = index * CHUNK_BYTES + 1
						local chunk = string.sub(raw, first, math.min(#raw, first + CHUNK_BYTES - 1))
						request("POST", `/v2/reverse/blob/mesh/{hash}/{index}`, chunk, {
							["Content-Type"] = "application/octet-stream",
							["X-Roblox-Sync-Transfer"] = transferId,
						})
					end
				end
				if ordinaryTransfer then
					for hash, raw in ordinaryTransfer.imageBlobs do
						local chunkCount = math.max(1, math.ceil(#raw / CHUNK_BYTES))
						for index = 0, chunkCount - 1 do
							local first = index * CHUNK_BYTES + 1
							local chunk = string.sub(raw, first, math.min(#raw, first + CHUNK_BYTES - 1))
							request("POST", `/v2/reverse/blob/image/{hash}/{index}`, chunk, {
								["Content-Type"] = "application/octet-stream",
								["X-Roblox-Sync-Transfer"] = transferId,
							})
						end
					end
				end
				return HttpService:JSONDecode(request("POST", "/v2/reverse/commit", HttpService:JSONEncode({
					transferId = transferId,
				})))
			end, debug.traceback)
			if sent then
				for _, operation in operations do
					operation:SetAttribute("BlenderObjectId", sourceGuids[operation])
				end
				print(`[Blender Mesh Sync][Union Phase 4][PASS] Sent incoming revision {sendResult.revision}`)
				setStatus(`Union Phase 4送信成功（Revision {sendResult.revision}、CSG {#phase1Document.csg}、リーフ {#phase1Document.instances}）。`, false)
			else
				warn("[Blender Mesh Sync][Union Phase 4] Send failed:\n" .. tostring(sendResult))
				setStatus("Union Phase 4の送信に失敗しました。Outputを確認してください。", true)
			end
		end
	end

	local function sendCurrentSelectionToBlender()
		local selected = Selection:Get()
		if #selected == 0 then
			setStatus("Blenderへ送るオブジェクトを選択してください。", true)
			return
		end

		local baseParts = {}
		local seenBaseParts = {}
		local function includeBasePart(instance: Instance)
			if instance:IsA("BasePart") and not seenBaseParts[instance] then
				seenBaseParts[instance] = true
				table.insert(baseParts, instance)
			end
		end
		for _, instance in selected do
			includeBasePart(instance)
			if instance:IsA("Model") or instance:IsA("Folder") then
				for _, descendant in instance:GetDescendants() do
					includeBasePart(descendant)
				end
			end
		end

		local operations = {}
		local ordinaryParts = {}
		for _, part in baseParts do
			if part:IsA("UnionOperation") or part:IsA("IntersectOperation") then
				table.insert(operations, part)
			else
				table.insert(ordinaryParts, part)
			end
		end

		if #operations == 0 then
			sendSelectionToBlender()
		else
			local excludedOperations = {}
			for _, operation in operations do excludedOperations[operation] = true end
			local collected, document, meshBlobs, imageBlobs, skippedObjects, propertyOnlyWarnings, hierarchyIds = xpcall(function()
				return collectStudioSelection(excludedOperations, true)
			end, debug.traceback)
			if not collected then
				warn("Studio to Blender mixed selection failed:\n" .. tostring(document))
				if string.find(tostring(document), "所有権または共有権限") then
					setStatus("メッシュを所有権または共有権限の関係で送れません。Blenderには送信していません。", true)
				else
					setStatus("混在選択の送信準備に失敗しました。Outputを確認してください。", true)
				end
				return
			end
			sendCsgSelectionToBlender(operations, {
				document = document,
				meshBlobs = meshBlobs,
				imageBlobs = imageBlobs,
				skippedObjects = skippedObjects,
				propertyOnlyWarnings = propertyOnlyWarnings,
				hierarchyIds = hierarchyIds,
			})
		end
	end

	local function createEditableMesh(record)
		local payload = expectTable(HttpService:JSONDecode(fetchBlob("mesh", record)), "mesh payload")
		local vertices = expectTable(payload.vertices, "mesh.vertices")
		local triangles = expectTable(payload.triangles, "mesh.triangles")
		local normals = expectTable(payload.cornerNormals or {}, "mesh.cornerNormals")
		local uvs = expectTable(payload.cornerUvs or {}, "mesh.cornerUvs")
		local colors = expectTable(payload.cornerColors or {}, "mesh.cornerColors")
		if #vertices == 0 or #triangles == 0 or #triangles > 20000 then
			error("Mesh payload has invalid geometry counts")
		end

		local editableMesh = AssetService:CreateEditableMesh()
		if not editableMesh then
			error("CreateEditableMesh returned nil in this Studio plugin session.")
		end
		local vertexIds = table.create(#vertices)
		for index, positionValue in vertices do
			local position = expectTable(positionValue, `vertices[{index}]`)
			vertexIds[index] = editableMesh:AddVertex(Vector3.new(
				expectNumber(position[1], "position.x"),
				expectNumber(position[2], "position.y"),
				expectNumber(position[3], "position.z")
			))
		end

		for index, triangleValue in triangles do
			local triangle = expectTable(triangleValue, `triangles[{index}]`)
			local first = vertexIds[expectNumber(triangle[1], "triangle[1]") + 1]
			local second = vertexIds[expectNumber(triangle[2], "triangle[2]") + 1]
			local third = vertexIds[expectNumber(triangle[3], "triangle[3]") + 1]
			if not first or not second or not third then
				editableMesh:Destroy()
				error(`triangles[{index}] references a missing vertex`)
			end
			local faceId = editableMesh:AddTriangle(first, second, third)

			if normals[index] then
				local ids = table.create(3)
				for corner = 1, 3 do
					local value = expectTable(normals[index][corner], "corner normal")
					ids[corner] = editableMesh:AddNormal(Vector3.new(value[1], value[2], value[3]))
				end
				editableMesh:SetFaceNormals(faceId, ids)
			end
			if uvs[index] then
				local ids = table.create(3)
				for corner = 1, 3 do
					local value = expectTable(uvs[index][corner], "corner uv")
					ids[corner] = editableMesh:AddUV(Vector2.new(value[1], value[2]))
				end
				editableMesh:SetFaceUVs(faceId, ids)
			end
			if colors[index] then
				local ids = table.create(3)
				for corner = 1, 3 do
					local value = expectTable(colors[index][corner], "corner color")
					ids[corner] = editableMesh:AddColor(
						Color3.new(value[1], value[2], value[3]),
						if value[4] ~= nil then value[4] else 1
					)
				end
				editableMesh:SetFaceColors(faceId, ids)
			end
		end
		return editableMesh
	end

	local function uploadAsset(object, assetType, name: string): number
		local ok, result, idOrError = pcall(function()
			return AssetService:CreateAssetAsync(object, assetType, {
				Name = string.sub(name, 1, 50),
				Description = "Created by Blender Mesh Sync",
			})
		end)
		if not ok then
			local uploadError = tostring(result)
			if string.find(string.lower(uploadError), "not available yet", 1, true) then
				error(`Asset upload failed: {uploadError}. Enable File > Beta Features > CreateAssetAsync Lua API, then restart Studio.`)
			end
			error(`Asset upload failed: {uploadError}`)
		end
		if result ~= Enum.CreateAssetResult.Success or type(idOrError) ~= "number" then
			error(`Asset upload failed: {tostring(result)} {tostring(idOrError)}`)
		end
		return idOrError
	end

	local function createMeshPart(assetId: number, collisionName: string): MeshPart
		local collision = Enum.CollisionFidelity[collisionName] or Enum.CollisionFidelity.Hull
		local lastError = ""
		for attempt = 1, 5 do
			local ok, result = pcall(function()
				return AssetService:CreateMeshPartAsync(Content.fromAssetId(assetId), {
					CollisionFidelity = collision,
				})
			end)
			if ok and result then
				return result
			end
			lastError = tostring(result)
			task.wait(attempt)
		end
		error(`Could not create MeshPart for asset {assetId}: {lastError}`)
	end

	local function ensureMeshAsset(record): (number, boolean)
		local hash = expectString(record.hash, "mesh.hash")
		local cached = meshCache[hash]
		if cached then
			local ok, template = pcall(createMeshPart, cached, "Hull")
			if ok and template then
				template:Destroy()
				return cached, true
			end
			meshCache[hash] = nil
		end
		if type(record.sourceUri) == "string" then
			local sourceId = tonumber(string.match(record.sourceUri, "(%d+)$"))
			if sourceId and sourceId > 0 then
				local ok, template = pcall(createMeshPart, sourceId, "Hull")
				if ok and template then
					template:Destroy()
					meshCache[hash] = sourceId
					saveCache(plugin, MESH_CACHE_KEY, meshCache)
					return sourceId, true
				end
			end
		end
		local editableMesh = createEditableMesh(record)
		local assetId = uploadAsset(editableMesh, Enum.AssetType.Mesh, record.name or `BlenderMesh_{string.sub(hash, 1, 8)}`)
		editableMesh:Destroy()
		meshCache[hash] = assetId
		saveCache(plugin, MESH_CACHE_KEY, meshCache)
		return assetId, false
	end

	local function ensureImageAsset(record): (number, boolean)
		local hash = expectString(record.hash, "image.hash")
		if imageCache[hash] then
			return imageCache[hash], true
		end
		if type(record.sourceUri) == "string" then
			local sourceId = tonumber(string.match(record.sourceUri, "(%d+)$"))
			if sourceId and sourceId > 0 then
				imageCache[hash] = sourceId
				saveCache(plugin, IMAGE_CACHE_KEY, imageCache)
				return sourceId, true
			end
		end
		local width = expectNumber(record.width, "image.width")
		local height = expectNumber(record.height, "image.height")
		if width < 1 or height < 1 or width > 1024 or height > 1024 then
			error(`Image {record.name or hash} is outside the 1024x1024 limit`)
		end
		local raw = fetchBlob("image", record)
		if #raw ~= width * height * 4 then
			error("Image RGBA byte count is invalid")
		end
		local editableImage = AssetService:CreateEditableImage({ Size = Vector2.new(width, height) })
		if not editableImage then
			error("CreateEditableImage returned nil in this Studio plugin session.")
		end
		editableImage:WritePixelsBuffer(Vector2.zero, Vector2.new(width, height), buffer.fromstring(raw))
		local assetId = uploadAsset(editableImage, Enum.AssetType.Image, record.name or `BlenderImage_{string.sub(hash, 1, 8)}`)
		editableImage:Destroy()
		imageCache[hash] = assetId
		saveCache(plugin, IMAGE_CACHE_KEY, imageCache)
		return assetId, false
	end

	local function applyAppearance(part: MeshPart, appearance, imageAssets, stagedVariants)
		appearance = expectTable(appearance, "appearance")
		local mode = expectString(appearance.mode, "appearance.mode")
		local maps = expectTable(appearance.maps or {}, "appearance.maps")
		part.Transparency = math.clamp(appearance.transparency or 0, 0, 1)
		part.Material = Enum.Material.SmoothPlastic
		part.Color = Color3.new(1, 1, 1)
		part.TextureID = ""

		if mode == "MATERIAL" then
			part.Material = Enum.Material[appearance.material] or Enum.Material.Plastic
			part.Color = colorFromArray(appearance.color, Color3.fromRGB(163, 162, 165))
		elseif mode == "TEXTURE" then
			local variantData = appearance.materialVariant
			if type(variantData) == "table" then
				local variantName = `RPS_{variantData.name or "Variant"}_{string.sub(appearance.hash or "00000000", 1, 8)}`
				local variant = stagedVariants[variantName]
				if not variant then
					variant = Instance.new("MaterialVariant")
					variant.Name = variantName
					variant:SetAttribute("GeneratedByBlenderSync", true)
					variant:SetAttribute("BlenderAppearanceHash", appearance.hash or "")
					stagedVariants[variantName] = variant
				end
				variant.BaseMaterial = Enum.Material[variantData.baseMaterial] or Enum.Material.Plastic
				variant.StudsPerTile = math.max(0.001, variantData.studsPerTile or 1)
				if maps.baseColor then variant.ColorMap = assetUri(imageAssets[maps.baseColor]) end
				if maps.roughness then variant.RoughnessMap = assetUri(imageAssets[maps.roughness]) end
				if maps.metallic then variant.MetalnessMap = assetUri(imageAssets[maps.metallic]) end
				if maps.normal then variant.NormalMap = assetUri(imageAssets[maps.normal]) end
				pcall(function()
					variant.MaterialPattern = Enum.MaterialPattern[variantData.materialPattern] or Enum.MaterialPattern.Regular
					variant.AlphaMode = Enum.AlphaMode[variantData.alphaMode] or Enum.AlphaMode.Overlay
					variant.EmissiveStrength = variantData.emissiveStrength or 0
					variant.EmissiveTint = colorFromArray(variantData.emissiveTint, Color3.new(1, 1, 1))
				end)
				part.Material = variant.BaseMaterial
				part.MaterialVariant = variant.Name
				part.Color = colorFromArray(appearance.color, Color3.new(1, 1, 1))
				return
			end
			local hasPbr = maps.roughness ~= nil or maps.metallic ~= nil or maps.normal ~= nil
			local useSurfaceAppearance = hasPbr or maps.emissive ~= nil
				or appearance.textureSource == "SURFACE_APPEARANCE"
			if not useSurfaceAppearance and maps.baseColor then
				part.TextureID = assetUri(imageAssets[maps.baseColor])
				part.Color = colorFromArray(appearance.color, Color3.new(1, 1, 1))
			else
				local surface = Instance.new("SurfaceAppearance")
				surface.Name = "BlenderSurfaceAppearance"
				if maps.baseColor then surface.ColorMap = assetUri(imageAssets[maps.baseColor]) end
				if maps.roughness then surface.RoughnessMap = assetUri(imageAssets[maps.roughness]) end
				if maps.metallic then surface.MetalnessMap = assetUri(imageAssets[maps.metallic]) end
				if maps.normal then surface.NormalMap = assetUri(imageAssets[maps.normal]) end
				surface.Color = colorFromArray(appearance.color, Color3.new(1, 1, 1))
				pcall(function()
					surface.AlphaMode = Enum.AlphaMode[appearance.alphaMode] or surface.AlphaMode
					surface.EmissiveStrength = appearance.emissiveStrength or 0
					surface.EmissiveTint = colorFromArray(appearance.emissiveTint, Color3.new(1, 1, 1))
					if maps.emissive then surface.EmissiveMaskContent = Content.fromAssetId(imageAssets[maps.emissive]) end
				end)
				surface.Parent = part
			end
			if not maps.baseColor then
				part.Color = colorFromArray(appearance.color, Color3.new(1, 1, 1))
			end
		elseif mode == "VERTEX" or mode == "NONE" then
			part.Color = Color3.new(1, 1, 1)
		else
			error(`Unsupported appearance mode: {mode}`)
		end
	end

	local function findTargetContainer(modelId: string, rootKind: string): Instance?
		for _, child in workspace:GetChildren() do
			if (child:IsA("Folder") or child:IsA("Model")) and child:GetAttribute("BlenderModelId") == modelId
				and child:GetAttribute("GeneratedByBlenderSync") == true
				and (child:GetAttribute("BlenderSchema") == SCHEMA_ID
					or child:GetAttribute("BlenderSchema") == PREVIOUS_SCHEMA_ID
					or child:GetAttribute("BlenderSchema") == LEGACY_SCHEMA_ID
					or child:GetAttribute("BlenderSchema") == ORIGINAL_SCHEMA_ID) then
				return child
			end
		end
		-- A Studio-origin Model can be nested below another Model, Folder, or even
		-- a Part. It is the synchronization boundary itself and must not be moved
		-- into a newly generated Folder when data returns from Blender.
		if rootKind == "STUDIO_SELECTION" then
			for _, descendant in workspace:GetDescendants() do
				if (descendant:IsA("Folder") or descendant:IsA("Model"))
					and descendant:GetAttribute("BlenderModelId") == modelId then
					return descendant
				end
			end
		end
		return nil
	end

	local function isGeneratedDocumentWrapper(instance: Instance?, modelId: string): boolean
		return instance ~= nil
			and (instance:IsA("Folder") or instance:IsA("Model"))
			and instance:GetAttribute("GeneratedByBlenderSync") == true
			and instance:GetAttribute("BlenderModelId") == modelId
			and type(instance:GetAttribute("BlenderHierarchyId")) ~= "string"
	end

	local function createPrimitivePart(partTypeName: string): BasePart
		if partTypeName == "Wedge" then return Instance.new("WedgePart") end
		if partTypeName == "CornerWedge" then return Instance.new("CornerWedgePart") end
		local part = Instance.new("Part")
		local shape = Enum.PartType[partTypeName]
		if not shape or (partTypeName ~= "Block" and partTypeName ~= "Ball" and partTypeName ~= "Cylinder") then
			error(`Unsupported Blender Part type: {partTypeName}`)
		end
		part.Shape = shape
		return part
	end

	local function applyPrimitiveAppearance(part: BasePart, instanceData)
		part.Material = Enum.Material[expectString(instanceData.material, "instance.material")] or Enum.Material.Plastic
		part.Color = colorFromArray(instanceData.color, Color3.fromRGB(163, 162, 165))
		part.Transparency = math.clamp(instanceData.transparency or 0, 0, 1)
	end

	local function preserveStudioAppearance(part: BasePart, old: BasePart)
		part.Material = old.Material
		part.MaterialVariant = old.MaterialVariant
		part.Color = old.Color
		part.Transparency = old.Transparency
		part.Reflectance = old.Reflectance
		if part:IsA("MeshPart") and old:IsA("MeshPart") then
			pcall(function() part.TextureID = old.TextureID end)
			pcall(function() (part :: any).TextureContent = (old :: any).TextureContent end)
		end
		for _, child in part:GetChildren() do
			if child:IsA("SurfaceAppearance") then child:Destroy() end
		end
		for _, child in old:GetChildren() do
			if child:IsA("SurfaceAppearance") then
				local ok, clone = pcall(function() return child:Clone() end)
				if ok and clone then clone.Parent = part end
			end
		end
		part:SetAttribute("BlenderAppearancePreserved", true)
	end

	local function preserveDisabledTransformChannels(part: BasePart, old: BasePart?, transformMask)
		if not old then return end
		local incoming = part.CFrame
		local previous = old.CFrame
		local position = if transformMask.position ~= false then incoming.Position else previous.Position
		local rotation = if transformMask.rotation ~= false
			then incoming - incoming.Position
			else previous - previous.Position
		part.CFrame = CFrame.new(position) * rotation
		if transformMask.scale == false then part.Size = old.Size end
	end

	-- Instance:Destroy() locks Parent to nil, so ChangeHistoryService cannot
	-- restore that Instance during Undo.  DataModel removals performed inside an
	-- import recording must only unparent the old Instance; Studio's undo record
	-- retains it and can put the exact same object back.
	local function removeUndoably(instance: Instance)
		instance.Parent = nil
	end

	local function ensureHierarchy(rootFolder: Instance, hierarchy, protectedRootIds: { [string]: boolean }?): { [string]: Instance }
		local existingById = {}
		for _, descendant in rootFolder:GetDescendants() do
			local nodeId = descendant:GetAttribute("BlenderHierarchyId")
			if type(nodeId) == "string" and (descendant:IsA("Folder") or descendant:IsA("Model")) then
				existingById[nodeId] = descendant
			end
		end
		local nodeById = {}
		for index, value in hierarchy do
			local node = expectTable(value, `hierarchy[{index}]`)
			nodeById[expectString(node.id, "hierarchy.id")] = node
		end
		-- The first Studio -> Blender round trip starts with the selected Model
		-- outside the generated sync Folder. Search the rest of Workspace by the
		-- incoming hierarchy GUIDs so that Model is adopted instead of duplicated
		-- and its Parts do not fall back to the sync root.
		for _, descendant in workspace:GetDescendants() do
			local nodeId = descendant:GetAttribute("BlenderHierarchyId")
			if type(nodeId) == "string" and nodeById[nodeId] and not existingById[nodeId]
				and (descendant:IsA("Folder") or descendant:IsA("Model")) then
				existingById[nodeId] = descendant
			end
		end
		local byId = {}
		local processed = {}
		local function protectedInstance(instance: Instance): boolean
			if not protectedRootIds then return false end
			for protectedId in protectedRootIds do
				if byId[protectedId] == instance then return true end
			end
			return false
		end
		local function assignParent(current: Instance, parent: Instance)
			if current == parent then
				error("Blender hierarchy resolved one container as its own parent")
			end
			if parent:IsDescendantOf(current) then
				-- Reversing an existing A > B relationship into B > A cannot be
				-- performed in one Parent assignment: Studio rejects the temporary
				-- cycle. Detach B first, then apply the requested relationship. A
				-- selected complete-sync boundary is never moved, so preserve the
				-- existing ancestor relationship if B is that protected boundary.
				if protectedInstance(parent) then
					warn(`[Blender Mesh Sync] Preserved complete-sync boundary {parent:GetFullName()} to avoid a circular hierarchy move`)
					return
				end
				parent.Parent = current.Parent or rootFolder
			end
			current.Parent = parent
		end
		local function resolvedParent(node): Instance
			local ancestorId = node.parentId or node.primaryCollectionId
			local visited = {}
			while ancestorId do
				if visited[ancestorId] then error("Blender hierarchy contains a cycle") end
				visited[ancestorId] = true
				if byId[ancestorId] then return byId[ancestorId] end
				local ancestor = nodeById[ancestorId]
				if not ancestor then break end
				ancestorId = ancestor.parentId or ancestor.primaryCollectionId
			end
			return rootFolder
		end
		local pending = table.clone(hierarchy)
		while #pending > 0 do
			local progressed = false
			for index = #pending, 1, -1 do
				local node = expectTable(pending[index], `hierarchy[{index}]`)
				local dependencyId = node.parentId or node.primaryCollectionId
				if not dependencyId or processed[dependencyId] then
					local nodeId = expectString(node.id, "hierarchy.id")
					local nodeKind = expectString(node.kind, "hierarchy.kind")
					if nodeKind ~= "MODEL" and nodeKind ~= "FOLDER" then
						error(`Unsupported Blender hierarchy kind: {nodeKind}`)
					end
					local studioMode = if nodeKind == "FOLDER" then "FOLDER" else node.studioMode or "MODEL"
					if studioMode ~= "MODEL" and studioMode ~= "FOLDER" and studioMode ~= "IGNORE" then
						error(`Unsupported Empty Studio mode: {tostring(studioMode)}`)
					end
					local parent = resolvedParent(node)
					local current = existingById[nodeId]
					if studioMode == "IGNORE" then
						-- Keep old generated containers untouched for backwards compatibility,
						-- but route this revision's descendants to the nearest visible parent.
						byId[nodeId] = parent
						processed[nodeId] = true
						table.remove(pending, index)
						progressed = true
						continue
					end
					if protectedRootIds and protectedRootIds[nodeId] and current then
						-- A selected complete-sync boundary represents an existing Studio
						-- Model. Its children are mirrored, but the boundary Instance itself
						-- must not be renamed, moved, replaced, or recreated.
						byId[nodeId] = current
						processed[nodeId] = true
						table.remove(pending, index)
						progressed = true
						continue
					end
					local expectedClass = if studioMode == "FOLDER" then "Folder" else "Model"
					if current == rootFolder then
						if current.ClassName ~= expectedClass then
							error(`Studio synchronization root cannot represent hierarchy kind {nodeKind}`)
						end
						current.Name = expectString(node.name, "hierarchy.name")
						current:SetAttribute("BlenderHierarchyKind", nodeKind)
						current:SetAttribute("BlenderStudioMode", studioMode)
						byId[nodeId] = current
						processed[nodeId] = true
						table.remove(pending, index)
						progressed = true
						continue
					end
					if current and current.ClassName ~= expectedClass then
						local replacement = Instance.new(expectedClass)
						replacement.Parent = current.Parent
						for _, child in current:GetChildren() do child.Parent = replacement end
						removeUndoably(current)
						current = replacement
					end
					if not current then current = Instance.new(expectedClass) end
					current.Name = expectString(node.name, "hierarchy.name")
					current:SetAttribute("GeneratedByBlenderSync", true)
					current:SetAttribute("BlenderHierarchyId", nodeId)
					current:SetAttribute("BlenderHierarchyKind", nodeKind)
					current:SetAttribute("BlenderStudioMode", studioMode)
					current:SetAttribute("BlenderOriginalParentId", node.parentId)
					current:SetAttribute(
						"BlenderCollectionIds",
						HttpService:JSONEncode(node.collectionIds or {})
					)
					if nodeKind == "MODEL" and node.cframe then
						local emptyCFrame = CFrame.new(table.unpack(expectTable(node.cframe, "hierarchy.cframe")))
						current:SetAttribute("BlenderEmptyCFrame", emptyCFrame)
						if current:IsA("Model") then current:PivotTo(emptyCFrame) end
					end
					assignParent(current, parent)
					byId[nodeId] = current
					processed[nodeId] = true
					table.remove(pending, index)
					progressed = true
				end
			end
			if not progressed then error("Blender hierarchy contains a cycle or missing parent") end
		end
		return byId
	end

	local function resolveInstanceHierarchyParent(nodeById, parentId, primaryCollectionId): string?
		if not parentId then return primaryCollectionId end
		if not primaryCollectionId then return parentId end
		if parentId == primaryCollectionId then return parentId end

		local function isDescendant(candidateId, ancestorId): boolean
			local currentId = candidateId
			local visited = {}
			while currentId and not visited[currentId] do
				if currentId == ancestorId then return true end
				visited[currentId] = true
				local current = nodeById[currentId]
				if not current then return false end
				currentId = current.parentId or current.primaryCollectionId
			end
			return false
		end

		-- Blender stores an Object parent (Model/Empty) and Collection membership
		-- separately. Studio can only give a Part one Parent, so choose whichever
		-- candidate is deeper in the original mixed Model/Folder hierarchy.
		if isDescendant(primaryCollectionId, parentId) then return primaryCollectionId end
		if isDescendant(parentId, primaryCollectionId) then return parentId end
		return parentId
	end

	local function importRevision(force: boolean?)
		if importing then
			return
		end
		importing = true
		local progress = {
			stage = "receiving revision",
			uploadedMeshes = 0,
			reusedMeshes = 0,
			uploadedImages = 0,
			reusedImages = 0,
		}
		local ok, message = xpcall(function()
			local revisionData = expectTable(fetchJson("/v1/revision"), "revision response")
			local revision = expectNumber(revisionData.revision, "revision")
			if not force and revision <= lastRevision then
				return "No new Blender revision"
			end
			if revision <= 0 then
				return "Connected. Send selected Parts or MeshParts from Blender."
			end
			setStatus(`Receiving Blender revision {revision}...`, false)
			progress.stage = "validating manifest"
			local document = expectTable(fetchJson("/v1/manifest"), "document")
			if document.schema ~= SCHEMA_ID and document.schema ~= PREVIOUS_SCHEMA_ID
				and document.schema ~= LEGACY_SCHEMA_ID and document.schema ~= ORIGINAL_SCHEMA_ID then
				error(`Unsupported Blender schema {tostring(document.schema)}; Studio plugin {PLUGIN_VERSION} accepts schemas 1 through 4`)
			end
			if document.revision ~= revision then
				error(`Mesh Sync revision mismatch: announced {revision}, received {tostring(document.revision)}`)
			end
			local modelData = expectTable(document.model, "model")
			local meshes = expectTable(document.meshes, "meshes")
			local images = expectTable(document.images, "images")
			local appearances = expectTable(document.appearances, "appearances")
			local instances = expectTable(document.instances, "instances")
			local manifestIncomingIds = {}
			for index, instanceData in instances do
				local instanceId = expectString(instanceData.id, `instances[{index}].id`)
				if manifestIncomingIds[instanceId] then error("Manifest contains duplicate instance IDs") end
				manifestIncomingIds[instanceId] = true
			end
			local replacementIds = {}
			for index, instanceData in instances do
				for replacementIndex, replacementIdValue in expectTable(
					instanceData.replacesObjectIds or {}, `instances[{index}].replacesObjectIds`
				) do
					local replacementId = expectString(replacementIdValue,
						`instances[{index}].replacesObjectIds[{replacementIndex}]`)
					if replacementId == instanceData.id or manifestIncomingIds[replacementId] then
						error(`instances[{index}] cannot replace an object included in the same revision`)
					end
					if replacementIds[replacementId] then
						error(`Replacement object ID {replacementId} is claimed more than once`)
					end
					replacementIds[replacementId] = true
				end
			end
			local hierarchy = expectTable(document.hierarchy or {}, "hierarchy")
			local hierarchyNodeById = {}
			for index, value in hierarchy do
				local node = expectTable(value, `hierarchy[{index}]`)
				local nodeId = expectString(node.id, `hierarchy[{index}].id`)
				if hierarchyNodeById[nodeId] then error("Manifest contains duplicate hierarchy IDs") end
				if node.kind ~= "MODEL" and node.kind ~= "FOLDER" then
					error(`hierarchy[{index}] has an unsupported kind`)
				end
				hierarchyNodeById[nodeId] = node
			end
			local hierarchyVisitState = {}
			local function visitHierarchy(nodeId: string)
				local state = hierarchyVisitState[nodeId]
				if state == 1 then error("Manifest hierarchy contains a cycle") end
				if state == 2 then return end
				hierarchyVisitState[nodeId] = 1
				local node = hierarchyNodeById[nodeId]
				local dependencyId = node.parentId or node.primaryCollectionId
				if dependencyId ~= nil then
					dependencyId = expectString(dependencyId, `hierarchy parent of {nodeId}`)
					if not hierarchyNodeById[dependencyId] then
						error(`Hierarchy node {nodeId} references a missing parent {dependencyId}`)
					end
					visitHierarchy(dependencyId)
				end
				hierarchyVisitState[nodeId] = 2
			end
			for nodeId in hierarchyNodeById do
				visitHierarchy(nodeId)
			end
			local replaceScopes = expectTable(document.replaceScopes or {}, "replaceScopes")
			local replaceScopeIds = {}
			local replaceScopePresentSourceIds = {}
			for index, value in replaceScopes do
				local scope = expectTable(value, `replaceScopes[{index}]`)
				local hierarchyId = expectString(scope.hierarchyId, `replaceScopes[{index}].hierarchyId`)
				if scope.mode ~= "REPLACE_DESCENDANTS" then
					error(`replaceScopes[{index}] has an unsupported mode`)
				end
				local scopeNode = hierarchyNodeById[hierarchyId]
				if not scopeNode or scopeNode.kind ~= "MODEL" then
					error(`replaceScopes[{index}] must reference a Model hierarchy node`)
				end
				if replaceScopeIds[hierarchyId] then error("Manifest contains duplicate replace scopes") end
				replaceScopeIds[hierarchyId] = true
				local presentSourceIds = {}
				for sourceIndex, sourceId in expectTable(scope.presentSourceObjectIds or {},
					`replaceScopes[{index}].presentSourceObjectIds`) do
					presentSourceIds[expectString(sourceId,
						`replaceScopes[{index}].presentSourceObjectIds[{sourceIndex}]`)] = true
				end
				replaceScopePresentSourceIds[hierarchyId] = presentSourceIds
			end
			local transformMask = expectTable(document.transformMask or {}, "transformMask")
			if #instances == 0 or #instances > MAX_INSTANCES then
				error("Manifest has an invalid instance count")
			end

			local meshAssets = {}
			local imageAssets = {}
			local appearanceByHash = {}
			local uploadedMeshes, reusedMeshes = 0, 0
			local uploadedImages, reusedImages = 0, 0

			progress.stage = "uploading meshes"
			for _, record in meshes do
				local assetId, reused = ensureMeshAsset(record)
				meshAssets[record.hash] = assetId
				if reused then reusedMeshes += 1 else uploadedMeshes += 1 end
				progress.uploadedMeshes = uploadedMeshes
				progress.reusedMeshes = reusedMeshes
			end
			progress.stage = "uploading images"
			for _, record in images do
				local assetId, reused = ensureImageAsset(record)
				imageAssets[record.hash] = assetId
				if reused then reusedImages += 1 else uploadedImages += 1 end
				progress.uploadedImages = uploadedImages
				progress.reusedImages = reusedImages
			end
			for _, appearance in appearances do
				appearanceByHash[expectString(appearance.hash, "appearance.hash")] = appearance
			end

			progress.stage = "staging instances"
			local staged = table.create(#instances)
			local templates = {}
			local stagedVariants = {}
			local buildOk, buildError = xpcall(function()
				for index, instanceData in instances do
					local kind = instanceData.kind or "MESH"
					local part: BasePart
					local meshHash = nil
					local appearanceHash = nil
					if kind == "PART" then
						part = createPrimitivePart(expectString(instanceData.partType, `instances[{index}].partType`))
						applyPrimitiveAppearance(part, instanceData)
					elseif kind == "MESH" then
						meshHash = expectString(instanceData.meshHash, `instances[{index}].meshHash`)
						local collisionName = instanceData.collisionFidelity or "Hull"
						local templateKey = `{meshHash}|{collisionName}`
						local template = templates[templateKey]
						if not template then
							template = createMeshPart(meshAssets[meshHash], collisionName)
							template.Archivable = true
							templates[templateKey] = template
						end
						part = template:Clone()
						appearanceHash = expectString(instanceData.appearanceHash, "instance.appearanceHash")
						applyAppearance(part :: MeshPart, appearanceByHash[appearanceHash], imageAssets, stagedVariants)
					else
						error(`Unsupported Blender instance kind: {tostring(kind)}`)
					end
					part.Name = expectString(instanceData.name, `instances[{index}].name`)
					local size = expectTable(instanceData.size, "instance.size")
					local cframe = expectTable(instanceData.cframe, "instance.cframe")
					part.Size = Vector3.new(size[1], size[2], size[3])
					part.CFrame = CFrame.new(table.unpack(cframe))
					part.Anchored = instanceData.anchored ~= false
					part.CanCollide = instanceData.canCollide ~= false
					part.CanTouch = instanceData.canTouch ~= false
					part.CanQuery = instanceData.canQuery ~= false
					part.CastShadow = instanceData.castShadow ~= false
					part:SetAttribute("GeneratedByBlenderSync", true)
					part:SetAttribute("BlenderGeometryOwner", "BLENDER")
					part:SetAttribute("BlenderObjectId", instanceData.id)
					part:SetAttribute("BlenderSourceObjectId", instanceData.sourceObjectId or instanceData.id)
					part:SetAttribute("BlenderDocumentModelId", modelData.id)
					part:SetAttribute("BlenderDocumentModelName", modelData.name)
					part:SetAttribute("BlenderDocumentRootKind", modelData.rootKind or "STUDIO_SELECTION")
					part:SetAttribute("BlenderPartType", if kind == "PART" then instanceData.partType else nil)
					part:SetAttribute("BlenderLogicalPartType", instanceData.logicalPartType)
					if kind == "MESH" then
						part:SetAttribute("BlenderMeshGroupId", instanceData.meshGroupId)
						part:SetAttribute("BlenderMeshHash", meshHash)
						part:SetAttribute("BlenderAppearanceHash", appearanceHash)
						part:SetAttribute("RobloxMeshAssetId", tostring(meshAssets[meshHash]))
					end
					part:SetAttribute("BlenderStateHash", instanceData.stateHash or "")
					part:SetAttribute("BlenderAppearanceAvailable", instanceData.appearanceAvailable ~= false)
					part:SetAttribute("BlenderOriginalParentId", instanceData.parentId)
					part:SetAttribute("BlenderCollectionIds", HttpService:JSONEncode(instanceData.collectionIds or {}))
					staged[index] = part
				end
			end, debug.traceback)
			for _, template in templates do template:Destroy() end
			if not buildOk then
				for _, part in staged do if part then part:Destroy() end end
				for _, variant in stagedVariants do variant:Destroy() end
				error(buildError)
			end

			progress.stage = "starting undo recording"
			local recording = ChangeHistoryService:TryBeginRecording("Sync Blender Mesh Instances")
			if not recording then
				for _, part in staged do part:Destroy() end
				for _, variant in stagedVariants do variant:Destroy() end
				error("Could not start Studio undo recording")
			end
			plugin:SetSetting(RECORDING_KEY, recording)
			local addedInstances, updatedInstances, removedInstances = 0, 0, 0
			progress.stage = "updating Workspace"
			local commitOk, commitError = xpcall(function()
				local modelId = expectString(modelData.id, "model.id")
				local rootKind = modelData.rootKind or "STUDIO_SELECTION"
				local previousRoot = findTargetContainer(modelId, rootKind)
				local rootFolder: Instance = workspace
				if previousRoot and rootKind == "STUDIO_SELECTION"
					and not isGeneratedDocumentWrapper(previousRoot, modelId) then
					-- The selected Studio Model is both the document boundary and the
					-- visible group. Keep its current parent exactly as-is.
					rootFolder = previousRoot
				elseif isGeneratedDocumentWrapper(previousRoot, modelId) then
					-- Older revisions materialized the transport document as a visible
					-- Studio Selection/BlenderModel Folder. It is not authored hierarchy:
					-- retain its membership as attributes, unwrap its children, and remove
					-- only this positively identified generated wrapper.
					for _, descendant in previousRoot:GetDescendants() do
						if descendant:IsA("BasePart") or descendant:IsA("Model") or descendant:IsA("Folder") then
							descendant:SetAttribute("BlenderDocumentModelId", modelId)
							descendant:SetAttribute("BlenderDocumentModelName", modelData.name)
							descendant:SetAttribute("BlenderDocumentRootKind", rootKind)
						end
					end
					for _, child in previousRoot:GetChildren() do child.Parent = workspace end
					removeUndoably(previousRoot)
				end
				if rootFolder ~= workspace then
					rootFolder:SetAttribute("GeneratedByBlenderSync", true)
					rootFolder:SetAttribute("BlenderModelId", modelId)
					rootFolder:SetAttribute("BlenderRootKind", rootKind)
					rootFolder:SetAttribute("BlenderSchema", SCHEMA_ID)
					rootFolder:SetAttribute("BlenderRevision", revision)
				end
				for variantName, variant in stagedVariants do
					local existingVariant = MaterialService:FindFirstChild(variantName)
					if existingVariant then
						if not existingVariant:IsA("MaterialVariant")
							or existingVariant:GetAttribute("GeneratedByBlenderSync") ~= true then
							error(`MaterialService already contains an unrelated instance named {variantName}`)
						end
						removeUndoably(existingVariant)
					end
					variant.Parent = MaterialService
				end
				-- Complete-sync documents include ancestors above the selected Empty so
				-- Studio knows where that boundary belongs. Those ancestors are context,
				-- not update targets: moving one can accidentally try to put an outer
				-- Model inside its own descendant. Protect the boundary and every
				-- incoming ancestor while still mirroring descendants below it.
				local protectedHierarchyIds = table.clone(replaceScopeIds)
				for scopeId in replaceScopeIds do
					local currentId = scopeId
					local visited = {}
					while currentId and not visited[currentId] do
						visited[currentId] = true
						protectedHierarchyIds[currentId] = true
						local currentNode = hierarchyNodeById[currentId]
						currentId = currentNode and (currentNode.parentId or currentNode.primaryCollectionId) or nil
					end
				end
				local hierarchyById = ensureHierarchy(rootFolder, hierarchy, protectedHierarchyIds)
				for _, hierarchyInstance in hierarchyById do
					if hierarchyInstance ~= workspace then
						hierarchyInstance:SetAttribute("BlenderDocumentModelId", modelId)
						hierarchyInstance:SetAttribute("BlenderDocumentModelName", modelData.name)
						hierarchyInstance:SetAttribute("BlenderDocumentRootKind", rootKind)
					end
				end
				local existingById = {}
				local existingParts = {}
				for _, child in rootFolder:GetDescendants() do
					local objectId = child:GetAttribute("BlenderObjectId")
					if type(objectId) == "string" and child:IsA("BasePart") then
						existingById[objectId] = child
						table.insert(existingParts, child)
					end
				end
				local incomingIds, incomingSourceIds = {}, {}
				for _, instanceData in instances do
					incomingIds[instanceData.id] = true
					incomingSourceIds[instanceData.sourceObjectId or instanceData.id] = true
				end
				-- As with hierarchy containers, the original Studio Parts may still be
				-- outside rootFolder on their first return from Blender. Reuse them by
				-- GUID so the update remains inside the adopted Studio Model.
				for _, child in workspace:GetDescendants() do
					local objectId = child:GetAttribute("BlenderObjectId")
					if type(objectId) == "string" and incomingIds[objectId]
						and child:IsA("BasePart") and not existingById[objectId] then
						existingById[objectId] = child
						table.insert(existingParts, child)
					end
				end
				for index, part in staged do
					local old = existingById[instances[index].id]
					local parentId = instances[index].parentId
					local primaryCollectionId = instances[index].primaryCollectionId
					preserveDisabledTransformChannels(part, old, transformMask)
					if old and instances[index].appearanceAvailable == false then
						preserveStudioAppearance(part, old)
					end
					local targetParentId = resolveInstanceHierarchyParent(
						hierarchyNodeById,
						parentId,
						primaryCollectionId
					)
					local targetParent = hierarchyById[targetParentId]
					if not targetParent and old then
						local previousParent = old.Parent
						if previousParent and previousParent ~= rootFolder
							and (previousParent:IsA("Model") or previousParent:IsA("Folder"))
							and previousParent:GetAttribute("GeneratedByBlenderSync") ~= true then
							-- A Studio-authored grouping may intentionally have no Blender
							-- Empty counterpart while Preserve Blender Hierarchy is enabled.
							-- Replacing the Part must not flatten that Studio-only Model/Folder.
							targetParent = previousParent
						end
					end
					part.Parent = targetParent or rootFolder
					if old then
						updatedInstances += 1
						removeUndoably(old)
					else
						addedInstances += 1
					end
				end
				for _, descendant in workspace:GetDescendants() do
					if descendant:IsA("BasePart") then
						local objectId = descendant:GetAttribute("BlenderObjectId")
						if type(objectId) == "string" and replacementIds[objectId] and descendant.Parent then
							removeUndoably(descendant)
							removedInstances += 1
						end
					end
				end
				-- A logical Blender object can expand to many Parts (Tube). Remove only
				-- stale generated components belonging to a logical object in this send.
				for _, oldPart in existingParts do
					if oldPart.Parent then
						local oldId = oldPart:GetAttribute("BlenderObjectId")
						local sourceId = oldPart:GetAttribute("BlenderSourceObjectId") or oldId
						if incomingSourceIds[sourceId] and not incomingIds[oldId] then removeUndoably(oldPart) end
					end
				end
				local incomingHierarchyIds = {}
				for _, node in hierarchy do
					if type(node.id) == "string" then incomingHierarchyIds[node.id] = true end
				end
				for scopeId in replaceScopeIds do
					local scopeRoot = hierarchyById[scopeId]
					local presentSourceIds = replaceScopePresentSourceIds[scopeId] or {}
					if not scopeRoot or not scopeRoot:IsA("Model") then
						error(`Complete sync scope {scopeId} could not resolve its existing Studio Model`)
					end
					-- Remove only geometry that previously participated in synchronization.
					-- Studio-only Parts and non-geometry children have no BlenderObjectId
					-- and are intentionally preserved.
					for _, descendant in scopeRoot:GetDescendants() do
						if descendant:IsA("BasePart") then
							local objectId = descendant:GetAttribute("BlenderObjectId")
							local sourceId = descendant:GetAttribute("BlenderSourceObjectId") or objectId
							if type(objectId) == "string" and objectId ~= ""
								and not incomingIds[objectId] and not presentSourceIds[sourceId]
								and descendant.Parent then
								removeUndoably(descendant)
								removedInstances += 1
							end
						end
					end
					-- Remove vanished synchronized child containers only when they are
					-- empty after geometry cleanup. Any Script, Light, Attachment, or
					-- Studio-only Part therefore protects its container automatically.
					local staleContainers = {}
					for _, descendant in scopeRoot:GetDescendants() do
						local hierarchyId = descendant:GetAttribute("BlenderHierarchyId")
						if type(hierarchyId) == "string" and hierarchyId ~= ""
							and not incomingHierarchyIds[hierarchyId]
							and (descendant:IsA("Model") or descendant:IsA("Folder")) then
							table.insert(staleContainers, descendant)
						end
					end
					table.sort(staleContainers, function(first, second)
						local function depth(instance: Instance): number
							local result = 0
							local current = instance.Parent
							while current and current ~= scopeRoot do
								result += 1
								current = current.Parent
							end
							return result
						end
						return depth(first) > depth(second)
					end)
					for _, container in staleContainers do
						if container.Parent and #container:GetChildren() == 0 then
							removeUndoably(container)
						end
					end
				end
				if rootFolder ~= workspace then
					local legacyGenerated = rootFolder:FindFirstChild("Generated Meshes")
					if legacyGenerated and #legacyGenerated:GetChildren() == 0 then removeUndoably(legacyGenerated) end
				end
				-- Select only the Parts/MeshParts created or replaced by this revision.
				-- Selecting the document root made ordinary move operations act on the
				-- entire Blender file instead of the objects the user just sent.
				Selection:Set(staged)
				return rootFolder
			end, debug.traceback)
			local finishOk, finishError = pcall(function()
				ChangeHistoryService:FinishRecording(
					recording,
					if commitOk then Enum.FinishRecordingOperation.Commit else Enum.FinishRecordingOperation.Cancel
				)
			end)
			if not finishOk then
				pcall(function()
					if ChangeHistoryService:IsRecordingInProgress(recording) then
						ChangeHistoryService:FinishRecording(recording, Enum.FinishRecordingOperation.Cancel)
					end
				end)
			end
			plugin:SetSetting(RECORDING_KEY, "")
			if not finishOk then
				for _, part in staged do if part.Parent == nil then part:Destroy() end end
				for _, variant in stagedVariants do if variant.Parent == nil then variant:Destroy() end end
				error(`Could not finish Studio undo recording: {tostring(finishError)}`)
			end
			if not commitOk then
				for _, part in staged do if part.Parent == nil then part:Destroy() end end
				for _, variant in stagedVariants do if variant.Parent == nil then variant:Destroy() end end
				error(commitError)
			end
			lastRevision = revision
			lastAnnouncedRevision = revision
			progress.stage = "complete"
			pcall(function()
				request("POST", "/v1/result", HttpService:JSONEncode({
					revision = revision,
					ok = true,
					instances = #instances,
					addedInstances = addedInstances,
					updatedInstances = updatedInstances,
					removedInstances = removedInstances,
					uploadedMeshes = uploadedMeshes,
					reusedMeshes = reusedMeshes,
					uploadedImages = uploadedImages,
					reusedImages = reusedImages,
				}))
			end)
			return `Revision {revision}: {addedInstances} added/{updatedInstances} updated/{removedInstances} removed | meshes {uploadedMeshes} new/{reusedMeshes} reused | images {uploadedImages} new/{reusedImages} reused`
		end, debug.traceback)
		importing = false
		if ok then
			setStatus(message, false)
		else
			warn("Blender Mesh Sync failed:\n" .. tostring(message))
			setStatus(`Sync failed during {progress.stage}. Outputを確認してください。`, true)
			pcall(function()
				request("POST", "/v1/result", HttpService:JSONEncode({
					ok = false,
					stage = progress.stage,
					error = tostring(message),
					uploadedMeshes = progress.uploadedMeshes,
					reusedMeshes = progress.reusedMeshes,
					uploadedImages = progress.uploadedImages,
					reusedImages = progress.reusedImages,
				}))
			end)
		end
	end

	local function connect(silent: boolean?): boolean
		if connecting then
			return false
		end
		connecting = true
		local ok, result = xpcall(function()
			local requestedPort = tonumber(ui.portBox.Text)
			if not requestedPort or requestedPort < 1024 or requestedPort > 65535
				or requestedPort % 1 ~= 0 then
				error("Port must be a whole number from 1024 to 65535")
			end
			local storedPort = nil
			local storedToken = nil
			if type(ui.connectionBox.Text) == "string" and ui.connectionBox.Text ~= "" then
				local parsed, parsedPort, parsedToken = pcall(parseConnection, ui.connectionBox.Text)
				if parsed then
					storedPort = parsedPort
					storedToken = parsedToken
				end
			end

			local authenticated = false
			local availableRevision = nil
			if storedPort == requestedPort and storedToken then
				port, token = storedPort, storedToken
				local authOk, revisionData = pcall(function()
					return fetchConnectionJson(port, token, "/v1/revision")
				end)
				if authOk then
					authenticated = true
					availableRevision = expectNumber(
						expectTable(revisionData, "revision response").revision,
						"revision"
					)
				end
			end
			if not authenticated then
				port, token = discoverPairing(requestedPort)
				ui.connectionBox.Text = `{port}|{token}`
			end
			ui.portBox.Text = tostring(port)
			plugin:SetSetting(PORT_KEY, port)
			local health = fetchConnectionJson(port, "", "/v1/health")
			if type(health) ~= "table" or health.ok ~= true then
				error("Blender Mesh Sync server did not answer")
			end
			if type(health.version) ~= "number" or health.version < 2 then
				error(`Blender add-on is too old for Studio plugin {PLUGIN_VERSION}`)
			end
			if type(health.schemas) ~= "table" or not table.find(health.schemas, SCHEMA_ID) then
				error(`Blender add-on does not support {SCHEMA_ID}; install add-on {PLUGIN_VERSION}`)
			end
			if availableRevision == nil then
				local revisionData = expectTable(
					fetchConnectionJson(port, token, "/v1/revision"),
					"revision response"
				)
				availableRevision = expectNumber(revisionData.revision, "revision")
			end
			plugin:SetSetting(CONNECTION_KEY, ui.connectionBox.Text)
			connected = true
			-- Treat the revision that already existed before this connection as the
			-- baseline. Opening a different place must not import Blender's previous
			-- send, while revisions created after this point still auto-sync.
			lastRevision = availableRevision
			lastAnnouncedRevision = availableRevision
			ui.connectButton.Text = "Disconnect"
			return "Connected. 接続後にBlenderから送られたrevisionを自動受信します。"
		end, debug.traceback)
		if ok then
			connectionLost = false
			setStatus(result, false)
		else
			connected = false
			ui.connectButton.Text = "Connect"
			if silent then
				setStatus("Blenderを待機中。初回は Allow Studio Connection を押してください。", false)
			else
				setStatus("接続待機中。Blenderで Allow Studio Connection を押してください。", true)
				warn("Blender Mesh Sync connection failed:\n" .. tostring(result))
			end
		end
		connecting = false
		return ok
	end

	ui.connectButton.MouseButton1Click:Connect(function()
		if connected then
			connected = false
			autoReconnect = false
			ui.connectButton.Text = "Connect"
			setStatus("Disconnected", false)
		else
			autoReconnect = true
			task.spawn(function()
				connect(false)
			end)
		end
	end)
	ui.forgetButton.MouseButton1Click:Connect(function()
		connected = false
		autoReconnect = false
		port = 0
		token = ""
		ui.connectionBox.Text = ""
		plugin:SetSetting(CONNECTION_KEY, "")
		ui.connectButton.Text = "Connect"
		setStatus("Connection forgotten. BlenderでAllow後にConnectしてください。", false)
	end)
	ui.syncButton.MouseButton1Click:Connect(function()
		task.spawn(function()
			if not connected and not connect(false) then
				return
			end
			importRevision(true)
		end)
	end)
	ui.exportButton.MouseButton1Click:Connect(function()
		task.spawn(function()
			if not connected and not connect(false) then
				return
			end
			sendCurrentSelectionToBlender()
		end)
	end)

	plugin.Unloading:Connect(function()
		stopped = true
		connected = false
	end)

	task.spawn(function()
		if autoReconnect then
			setStatus("保存済み接続でBlenderを探しています...", false)
		end
		while not stopped do
			if connected and not importing then
				local ok, revisionData = pcall(fetchJson, "/v1/revision")
				if ok and type(revisionData) == "table" and type(revisionData.revision) == "number" then
					if connectionLost then
						connectionLost = false
						setStatus("Reconnected to Blender.", false)
					end
					if revisionData.revision > lastRevision then
						lastAnnouncedRevision = revisionData.revision
						importRevision(false)
					end
				else
					if not connectionLost then
						connectionLost = true
						setStatus("Connection lost; retrying...", true)
					end
				end
			elseif autoReconnect and not connecting then
				connect(true)
			end
			task.wait(if connected then 1 else 2)
		end
	end)
end

return MeshSync
