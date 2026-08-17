local ChangeHistoryService = game:GetService("ChangeHistoryService")
local HttpService = game:GetService("HttpService")
local Selection = game:GetService("Selection")
local StudioService = game:GetService("StudioService")

local SCHEMA_ID = "roblox-primitives/1"
local MIN_PART_SIZE = 0.001
local MAX_PART_SIZE = 2048

local PART_TYPES = {
	Block = Enum.PartType.Block,
	Ball = Enum.PartType.Ball,
	Cylinder = Enum.PartType.Cylinder,
	Wedge = Enum.PartType.Wedge,
	CornerWedge = Enum.PartType.CornerWedge,
}

local toolbar = plugin:CreateToolbar("Primitive Sync")
local toolbarButton = toolbar:CreateButton(
	"PrimitiveSync",
	"Import Blender primitive JSON",
	""
)
toolbarButton.ClickableWhenViewportHidden = true

local widgetInfo = DockWidgetPluginGuiInfo.new(
	Enum.InitialDockState.Right,
	false,
	false,
	320,
	220,
	260,
	180
)
local widget = plugin:CreateDockWidgetPluginGuiAsync("RobloxPrimitiveSyncWidgetV1", widgetInfo)
widget.Title = "Roblox Primitive Sync 0.6.1"

local root = Instance.new("Frame")
root.Name = "Root"
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
layout.Padding = UDim.new(0, 10)
layout.SortOrder = Enum.SortOrder.LayoutOrder
layout.Parent = root

local title = Instance.new("TextLabel")
title.Name = "Title"
title.BackgroundTransparency = 1
title.Size = UDim2.new(1, 0, 0, 28)
title.Font = Enum.Font.BuilderSansBold
title.Text = "Blender Primitive Import"
title.TextSize = 18
title.TextXAlignment = Enum.TextXAlignment.Left
title.TextColor3 = Color3.fromRGB(235, 235, 235)
title.LayoutOrder = 1
title.Parent = root

local description = Instance.new("TextLabel")
description.Name = "Description"
description.BackgroundTransparency = 1
description.Size = UDim2.new(1, 0, 0, 40)
description.Font = Enum.Font.BuilderSans
description.Text = "Blenderから書き出した .rbxprimitives.json を標準Partとして読み込みます。"
description.TextWrapped = true
description.TextSize = 14
description.TextXAlignment = Enum.TextXAlignment.Left
description.TextYAlignment = Enum.TextYAlignment.Top
description.TextColor3 = Color3.fromRGB(190, 190, 190)
description.LayoutOrder = 2
description.Parent = root

local importButton = Instance.new("TextButton")
importButton.Name = "ImportButton"
importButton.Size = UDim2.new(1, 0, 0, 36)
importButton.BackgroundColor3 = Color3.fromRGB(0, 162, 255)
importButton.BorderSizePixel = 0
importButton.Font = Enum.Font.BuilderSansBold
importButton.Text = "Import JSON"
importButton.TextSize = 15
importButton.TextColor3 = Color3.new(1, 1, 1)
importButton.LayoutOrder = 3
importButton.Parent = root

local corner = Instance.new("UICorner")
corner.CornerRadius = UDim.new(0, 6)
corner.Parent = importButton

local statusLabel = Instance.new("TextLabel")
statusLabel.Name = "Status"
statusLabel.BackgroundTransparency = 1
statusLabel.Size = UDim2.new(1, 0, 0, 48)
statusLabel.Font = Enum.Font.BuilderSans
statusLabel.Text = "Ready"
statusLabel.TextWrapped = true
statusLabel.TextSize = 13
statusLabel.TextXAlignment = Enum.TextXAlignment.Left
statusLabel.TextYAlignment = Enum.TextYAlignment.Top
statusLabel.TextColor3 = Color3.fromRGB(170, 170, 170)
statusLabel.LayoutOrder = 4
statusLabel.Parent = root

local function setStatus(message: string, isError: boolean?)
	statusLabel.Text = message
	statusLabel.TextColor3 = if isError
		then Color3.fromRGB(255, 120, 120)
		else Color3.fromRGB(170, 220, 170)
end

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

local function expectBoolean(value: any, path: string, defaultValue: boolean): boolean
	if value == nil then
		return defaultValue
	end
	if type(value) ~= "boolean" then
		error(`{path} must be a boolean`)
	end
	return value
end

local function expectNumber(value: any, path: string): number
	if type(value) ~= "number" or value ~= value or value == math.huge or value == -math.huge then
		error(`{path} must be a finite number`)
	end
	return value
end

local function expectNumberArray(value: any, path: string, count: number): { number }
	local values = expectTable(value, path)
	if #values ~= count then
		error(`{path} must contain {count} numbers`)
	end
	local result = table.create(count)
	for index = 1, count do
		result[index] = expectNumber(values[index], `{path}[{index}]`)
	end
	return result
end

type ParsedPart = {
	id: string,
	name: string,
	partType: Enum.PartType,
	partTypeName: string,
	size: Vector3,
	cframe: CFrame,
	color: Color3,
	material: Enum.Material,
	transparency: number,
	anchored: boolean,
	canCollide: boolean,
	canTouch: boolean,
	canQuery: boolean,
	castShadow: boolean,
	groupId: string?,
	groupName: string?,
	groupType: string?,
	groupPartCount: number?,
	sourceObjectId: string?,
}

local function parsePart(rawValue: any, index: number): ParsedPart
	local path = `parts[{index}]`
	local raw = expectTable(rawValue, path)
	local partTypeName = expectString(raw.type, `{path}.type`)
	local partType = PART_TYPES[partTypeName]
	if not partType then
		error(`{path}.type is unsupported: {partTypeName}`)
	end

	local sizeValues = expectNumberArray(raw.size, `{path}.size`, 3)
	for axis, value in sizeValues do
		if value < MIN_PART_SIZE or value > MAX_PART_SIZE then
			error(`{path}.size[{axis}] is outside Roblox's supported range`)
		end
	end

	local cframeValues = expectNumberArray(raw.cframe, `{path}.cframe`, 12)
	local colorValues = expectNumberArray(raw.color, `{path}.color`, 3)
	for channel, value in colorValues do
		if value < 0 or value > 1 then
			error(`{path}.color[{channel}] must be between 0 and 1`)
		end
	end

	local materialName = expectString(raw.material, `{path}.material`)
	local material = Enum.Material[materialName]
	if not material then
		error(`{path}.material is unsupported: {materialName}`)
	end

	local transparency = if raw.transparency == nil
		then 0
		else expectNumber(raw.transparency, `{path}.transparency`)
	if transparency < 0 or transparency > 1 then
		error(`{path}.transparency must be between 0 and 1`)
	end

	local groupId, groupName, groupType, groupPartCount = nil, nil, nil, nil
	if raw.group ~= nil then
		local group = expectTable(raw.group, `{path}.group`)
		groupId = expectString(group.id, `{path}.group.id`)
		groupName = expectString(group.name, `{path}.group.name`)
		groupType = expectString(group.type, `{path}.group.type`)
		if groupType ~= "Tube" then
			error(`{path}.group.type is unsupported: {groupType}`)
		end
		local segments = expectNumber(group.segments, `{path}.group.segments`)
		local innerRatio = expectNumber(group.innerRatio, `{path}.group.innerRatio`)
		groupPartCount = expectNumber(group.partCount, `{path}.group.partCount`)
		if segments < 3 or segments > 64 or segments % 1 ~= 0 then
			error(`{path}.group.segments must be an integer from 3 to 64`)
		end
		if innerRatio <= 0 or innerRatio >= 1 then
			error(`{path}.group.innerRatio must be between 0 and 1`)
		end
		if groupPartCount ~= segments * 4 then
			error(`{path}.group.partCount must equal segments * 4`)
		end
	end

	return {
		id = expectString(raw.id, `{path}.id`),
		name = expectString(raw.name, `{path}.name`),
		partType = partType,
		partTypeName = partTypeName,
		size = Vector3.new(table.unpack(sizeValues)),
		cframe = CFrame.new(table.unpack(cframeValues)),
		color = Color3.new(table.unpack(colorValues)),
		material = material,
		transparency = transparency,
		anchored = expectBoolean(raw.anchored, `{path}.anchored`, true),
		canCollide = expectBoolean(raw.canCollide, `{path}.canCollide`, true),
		canTouch = expectBoolean(raw.canTouch, `{path}.canTouch`, true),
		canQuery = expectBoolean(raw.canQuery, `{path}.canQuery`, true),
		castShadow = expectBoolean(raw.castShadow, `{path}.castShadow`, true),
		groupId = groupId,
		groupName = groupName,
		groupType = groupType,
		groupPartCount = groupPartCount,
		sourceObjectId = if type(raw.sourceObjectId) == "string" then raw.sourceObjectId else nil,
	}
end

local function parseDocument(contents: string)
	local decoded = HttpService:JSONDecode(contents)
	local document = expectTable(decoded, "document")
	if document.schema ~= SCHEMA_ID then
		error(`Unsupported schema: {tostring(document.schema)}`)
	end

	local modelData = expectTable(document.model, "model")
	local rawParts = expectTable(document.parts, "parts")
	if #rawParts == 0 then
		error("The document contains no parts")
	end
	if #rawParts > 10000 then
		error("The document exceeds the 10,000 part safety limit")
	end

	local ids = {}
	local groups = {}
	local parts = table.create(#rawParts)
	for index, rawPart in rawParts do
		local parsed = parsePart(rawPart, index)
		if ids[parsed.id] then
			error(`Duplicate part id: {parsed.id}`)
		end
		ids[parsed.id] = true
		if parsed.groupId then
			local group = groups[parsed.groupId]
			if not group then
				group = { count = 0, expected = parsed.groupPartCount, name = parsed.groupName }
				groups[parsed.groupId] = group
			elseif group.expected ~= parsed.groupPartCount or group.name ~= parsed.groupName then
				error(`Inconsistent Tube group: {parsed.groupId}`)
			end
			group.count += 1
		end
		parts[index] = parsed
	end
	for groupId, group in groups do
		if group.count ~= group.expected then
			error(`Tube group {groupId} contains {group.count} of {group.expected} WedgeParts`)
		end
	end

	return {
		modelId = if type(modelData.id) == "string" then modelData.id else "",
		modelName = expectString(modelData.name, "model.name"),
		parts = parts,
	}
end

local function buildModel(document)
	local model = Instance.new("Model")
	model.Name = `BlenderSync_{document.modelName}`
	model:SetAttribute("GeneratedByBlenderSync", true)
	model:SetAttribute("BlenderModelId", document.modelId)
	model:SetAttribute("BlenderSchema", SCHEMA_ID)

	local generatedFolder = Instance.new("Folder")
	generatedFolder.Name = "Generated Parts"
	generatedFolder.Parent = model

	local studioOnlyFolder = Instance.new("Folder")
	studioOnlyFolder.Name = "Studio Only"
	studioOnlyFolder.Parent = model

	local groupedModels = {}
	for _, data: ParsedPart in document.parts do
		local parent: Instance = generatedFolder
		if data.groupId and data.groupType == "Tube" then
			local group = groupedModels[data.groupId]
			if not group then
				group = Instance.new("Model")
				group.Name = data.groupName or "Tube"
				group:SetAttribute("GeneratedByBlenderSync", true)
				group:SetAttribute("BlenderObjectId", data.groupId)
				group:SetAttribute("BlenderModelId", document.modelId)
				group:SetAttribute("BlenderPartType", "Tube")
				group.Parent = generatedFolder
				groupedModels[data.groupId] = group
			end
			parent = group
		end

		local part
		if data.partTypeName == "Wedge" then
			part = Instance.new("WedgePart")
		elseif data.partTypeName == "CornerWedge" then
			part = Instance.new("CornerWedgePart")
		else
			part = Instance.new("Part")
			part.Shape = data.partType
		end
		part.Name = data.name
		part.Size = data.size
		part.CFrame = data.cframe
		part.Color = data.color
		part.Material = data.material
		part.Transparency = data.transparency
		part.Anchored = data.anchored
		part.CanCollide = data.canCollide
		part.CanTouch = data.canTouch
		part.CanQuery = data.canQuery
		part.CastShadow = data.castShadow
		part:SetAttribute("BlenderObjectId", data.id)
		part:SetAttribute("BlenderModelId", document.modelId)
		part:SetAttribute("BlenderPartType", data.partTypeName)
		if data.sourceObjectId then
			part:SetAttribute("BlenderSourceObjectId", data.sourceObjectId)
		end
		part:SetAttribute("GeneratedByBlenderSync", true)
		part.Parent = parent
	end

	return model
end

local importInProgress = false

local function importJson()
	if importInProgress then
		return
	end
	importInProgress = true
	importButton.Active = false
	importButton.Text = "Importing..."
	setStatus("ファイルを選択してください", false)

	local ok, result = xpcall(function()
		local file = StudioService:PromptImportFileAsync({ "json" })
		if not file then
			return nil
		end
		if file.Size > 10 * 1024 * 1024 then
			error("JSON file exceeds the 10 MB safety limit")
		end

		local document = parseDocument(file:GetBinaryContents())
		local recording = ChangeHistoryService:TryBeginRecording("Import Blender Primitive Model")
		if not recording then
			error("Could not start Studio undo recording")
		end

		local buildOk, modelOrError = xpcall(function()
			local model = buildModel(document)
			model.Parent = workspace
			Selection:Set({ model })
			return model
		end, debug.traceback)

		ChangeHistoryService:FinishRecording(
			recording,
			if buildOk then Enum.FinishRecordingOperation.Commit else Enum.FinishRecordingOperation.Cancel
		)
		if not buildOk then
			error(modelOrError)
		end
		return #document.parts
	end, debug.traceback)

	if ok then
		if result == nil then
			setStatus("Import cancelled", false)
		else
			setStatus(`Imported {result} standard Parts`, false)
		end
	else
		warn("Roblox Primitive Sync import failed:\n" .. tostring(result))
		setStatus("Import failed. Outputを確認してください。", true)
	end

	importInProgress = false
	importButton.Active = true
	importButton.Text = "Import JSON"
end

importButton.MouseButton1Click:Connect(function()
	task.spawn(importJson)
end)

toolbarButton.Click:Connect(function()
	widget.Enabled = not widget.Enabled
	toolbarButton:SetActive(widget.Enabled)
end)

widget:GetPropertyChangedSignal("Enabled"):Connect(function()
	toolbarButton:SetActive(widget.Enabled)
end)

require(script.MeshSync).start(plugin)
