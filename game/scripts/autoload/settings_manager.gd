extends Node

const SETTINGS_PATH: String = "user://settings.cfg"

var fullscreen: bool = false
var vsync: bool = false
var render_scale: float = 1.0
var shadow_quality: int = 2
var fov: float = 85.0

var master_volume: float = 1.0
var music_volume: float = 0.7
var sfx_volume: float = 1.0
var ambience_volume: float = 0.8

var mouse_sensitivity: float = 0.002
var invert_y: bool = false

var hud_scale: float = 1.0
var hud_opacity: float = 1.0
var show_damage_numbers: bool = true

var _custom_bindings: Dictionary = {}
var _config: ConfigFile = ConfigFile.new()

const DEFAULT_BINDINGS: Dictionary = {
	"move_forward": {"type": "key", "keycode": KEY_W},
	"move_back": {"type": "key", "keycode": KEY_S},
	"move_left": {"type": "key", "keycode": KEY_A},
	"move_right": {"type": "key", "keycode": KEY_D},
	"sprint": {"type": "key", "keycode": KEY_SHIFT},
	"dodge": {"type": "key", "keycode": KEY_SPACE},
	"attack": {"type": "mouse", "button": MOUSE_BUTTON_LEFT},
	"heavy_attack": {"type": "mouse", "button": MOUSE_BUTTON_RIGHT},
	"interact": {"type": "key", "keycode": KEY_E},
	"heal": {"type": "key", "keycode": KEY_R},
	"spell_1": {"type": "key", "keycode": KEY_1},
	"spell_2": {"type": "key", "keycode": KEY_2},
	"spell_3": {"type": "key", "keycode": KEY_3},
	"spell_4": {"type": "key", "keycode": KEY_4},
	"pause_menu": {"type": "key", "keycode": KEY_ESCAPE},
	"inventory": {"type": "key", "keycode": KEY_TAB},
	"map": {"type": "key", "keycode": KEY_M},
	"journal": {"type": "key", "keycode": KEY_J},
	"call_horse": {"type": "key", "keycode": KEY_H},
}

signal settings_changed


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	_load_settings()
	_setup_input_actions()
	_apply_video_settings()
	_apply_audio_settings()


func _setup_input_actions() -> void:
	for action_name: String in DEFAULT_BINDINGS:
		if not InputMap.has_action(action_name):
			InputMap.add_action(action_name)
		else:
			InputMap.action_erase_events(action_name)
		var binding: Dictionary = _custom_bindings.get(action_name, DEFAULT_BINDINGS[action_name])
		var event := _create_input_event(binding)
		if event:
			InputMap.action_add_event(action_name, event)


func _create_input_event(binding: Dictionary) -> InputEvent:
	var type_str: String = binding.get("type", "")
	if type_str == "key":
		var event := InputEventKey.new()
		event.physical_keycode = binding.get("keycode", KEY_NONE) as Key
		return event
	elif type_str == "mouse":
		var event := InputEventMouseButton.new()
		event.button_index = binding.get("button", MOUSE_BUTTON_LEFT) as MouseButton
		return event
	return null


func remap_action(action_name: String, event: InputEvent) -> void:
	if action_name not in DEFAULT_BINDINGS:
		return
	InputMap.action_erase_events(action_name)
	InputMap.action_add_event(action_name, event)
	if event is InputEventKey:
		_custom_bindings[action_name] = {"type": "key", "keycode": (event as InputEventKey).physical_keycode}
	elif event is InputEventMouseButton:
		_custom_bindings[action_name] = {"type": "mouse", "button": (event as InputEventMouseButton).button_index}
	save_settings()


func get_action_event(action_name: String) -> InputEvent:
	var events := InputMap.action_get_events(action_name)
	if events.size() > 0:
		return events[0]
	return null


func get_action_display_name(action_name: String) -> String:
	var event := get_action_event(action_name)
	if event is InputEventKey:
		return OS.get_keycode_string((event as InputEventKey).physical_keycode)
	elif event is InputEventMouseButton:
		match (event as InputEventMouseButton).button_index:
			MOUSE_BUTTON_LEFT: return "LMB"
			MOUSE_BUTTON_RIGHT: return "RMB"
			MOUSE_BUTTON_MIDDLE: return "MMB"
			_: return "Mouse " + str((event as InputEventMouseButton).button_index)
	return "?"


func reset_to_defaults() -> void:
	_custom_bindings.clear()
	_setup_input_actions()
	save_settings()


func has_binding_conflict(action_name: String, event: InputEvent) -> String:
	for other_action: String in DEFAULT_BINDINGS:
		if other_action == action_name:
			continue
		var other_events := InputMap.action_get_events(other_action)
		for other_event: InputEvent in other_events:
			if event.is_match(other_event):
				return other_action
	return ""


func _apply_video_settings() -> void:
	if fullscreen:
		DisplayServer.window_set_mode(DisplayServer.WINDOW_MODE_FULLSCREEN)
	else:
		DisplayServer.window_set_mode(DisplayServer.WINDOW_MODE_WINDOWED)
	DisplayServer.window_set_vsync_mode(
		DisplayServer.VSYNC_ENABLED if vsync else DisplayServer.VSYNC_DISABLED
	)


func _apply_audio_settings() -> void:
	var master_idx := AudioServer.get_bus_index("Master")
	if master_idx >= 0:
		AudioServer.set_bus_volume_db(master_idx, linear_to_db(master_volume))


func save_settings() -> void:
	_config.set_value("video", "fullscreen", fullscreen)
	_config.set_value("video", "vsync", vsync)
	_config.set_value("video", "render_scale", render_scale)
	_config.set_value("video", "shadow_quality", shadow_quality)
	_config.set_value("video", "fov", fov)
	_config.set_value("audio", "master_volume", master_volume)
	_config.set_value("audio", "music_volume", music_volume)
	_config.set_value("audio", "sfx_volume", sfx_volume)
	_config.set_value("audio", "ambience_volume", ambience_volume)
	_config.set_value("input", "mouse_sensitivity", mouse_sensitivity)
	_config.set_value("input", "invert_y", invert_y)
	_config.set_value("input", "custom_bindings", _custom_bindings)
	_config.set_value("hud", "hud_scale", hud_scale)
	_config.set_value("hud", "hud_opacity", hud_opacity)
	_config.set_value("hud", "show_damage_numbers", show_damage_numbers)
	var err := _config.save(SETTINGS_PATH)
	if err == OK:
		settings_changed.emit()


func _load_settings() -> void:
	var err := _config.load(SETTINGS_PATH)
	if err != OK:
		return
	fullscreen = _config.get_value("video", "fullscreen", fullscreen)
	vsync = _config.get_value("video", "vsync", vsync)
	render_scale = _config.get_value("video", "render_scale", render_scale)
	shadow_quality = _config.get_value("video", "shadow_quality", shadow_quality)
	fov = _config.get_value("video", "fov", fov)
	master_volume = _config.get_value("audio", "master_volume", master_volume)
	music_volume = _config.get_value("audio", "music_volume", music_volume)
	sfx_volume = _config.get_value("audio", "sfx_volume", sfx_volume)
	ambience_volume = _config.get_value("audio", "ambience_volume", ambience_volume)
	mouse_sensitivity = _config.get_value("input", "mouse_sensitivity", mouse_sensitivity)
	invert_y = _config.get_value("input", "invert_y", invert_y)
	_custom_bindings = _config.get_value("input", "custom_bindings", {})
	hud_scale = _config.get_value("hud", "hud_scale", hud_scale)
	hud_opacity = _config.get_value("hud", "hud_opacity", hud_opacity)
	show_damage_numbers = _config.get_value("hud", "show_damage_numbers", show_damage_numbers)
