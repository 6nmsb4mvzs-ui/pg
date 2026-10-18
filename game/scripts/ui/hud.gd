extends CanvasLayer

var _health_bar: ProgressBar
var _stamina_bar: ProgressBar
var _mana_bar: ProgressBar
var _souls_label: Label
var _interact_label: Label
var _death_overlay: ColorRect
var _death_text: Label
var _damage_container: Control
var _player_connected: bool = false


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	var root := Control.new()
	root.set_anchors_preset(Control.PRESET_FULL_RECT)
	root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(root)
	_build_bars(root)
	_build_souls(root)
	_build_interaction(root)
	_build_death_overlay(root)
	_build_damage_container(root)
	GameManager.souls_changed.connect(_on_souls_changed)
	GameManager.state_changed.connect(_on_state_changed)
	GameManager.player_died.connect(_on_player_died)
	GameManager.player_respawned.connect(_on_player_respawned)


func _process(_delta: float) -> void:
	if not _player_connected and GameManager.player:
		_connect_player(GameManager.player)


func _connect_player(player: CharacterBody3D) -> void:
	_player_connected = true
	if player.has_signal("interaction_available"):
		player.interaction_available.connect(_on_interaction_available)
		player.interaction_cleared.connect(_on_interaction_cleared)
		player.attack_hit.connect(_on_attack_hit)
	var stats := player.get_node_or_null("PlayerStats")
	if stats:
		stats.health_changed.connect(_on_health_changed)
		stats.stamina_changed.connect(_on_stamina_changed)
		stats.mana_changed.connect(_on_mana_changed)
		_health_bar.max_value = stats.max_health
		_health_bar.value = stats.health
		_stamina_bar.max_value = stats.max_stamina
		_stamina_bar.value = stats.stamina
		_mana_bar.max_value = stats.max_mana
		_mana_bar.value = stats.mana


func _build_bars(parent: Control) -> void:
	var container := VBoxContainer.new()
	container.position = Vector2(20, 20)
	container.add_theme_constant_override("separation", 4)
	parent.add_child(container)
	_health_bar = _make_bar(Color(0.7, 0.12, 0.1), Vector2(250, 16))
	container.add_child(_health_bar)
	_stamina_bar = _make_bar(Color(0.15, 0.55, 0.2), Vector2(220, 12))
	container.add_child(_stamina_bar)
	_mana_bar = _make_bar(Color(0.15, 0.25, 0.7), Vector2(200, 10))
	container.add_child(_mana_bar)


func _make_bar(color: Color, min_size: Vector2) -> ProgressBar:
	var bar := ProgressBar.new()
	bar.max_value = 100.0
	bar.value = 100.0
	bar.show_percentage = false
	bar.custom_minimum_size = min_size
	var bg := StyleBoxFlat.new()
	bg.bg_color = Color(0.05, 0.05, 0.05, 0.8)
	bg.set_corner_radius_all(2)
	bar.add_theme_stylebox_override("background", bg)
	var fill := StyleBoxFlat.new()
	fill.bg_color = color
	fill.set_corner_radius_all(2)
	bar.add_theme_stylebox_override("fill", fill)
	return bar


func _build_souls(parent: Control) -> void:
	_souls_label = Label.new()
	_souls_label.text = "0"
	_souls_label.position = Vector2(20, 92)
	_souls_label.add_theme_font_size_override("font_size", 18)
	_souls_label.add_theme_color_override("font_color", Color(0.85, 0.75, 0.4))
	parent.add_child(_souls_label)


func _build_interaction(parent: Control) -> void:
	_interact_label = Label.new()
	_interact_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_interact_label.set_anchors_preset(Control.PRESET_CENTER_BOTTOM)
	_interact_label.offset_left = -150
	_interact_label.offset_right = 150
	_interact_label.offset_top = -80
	_interact_label.offset_bottom = -50
	_interact_label.add_theme_font_size_override("font_size", 16)
	_interact_label.add_theme_color_override("font_color", Color(0.9, 0.85, 0.7))
	_interact_label.visible = false
	parent.add_child(_interact_label)


func _build_death_overlay(parent: Control) -> void:
	_death_overlay = ColorRect.new()
	_death_overlay.set_anchors_preset(Control.PRESET_FULL_RECT)
	_death_overlay.color = Color(0, 0, 0, 0)
	_death_overlay.visible = false
	_death_overlay.mouse_filter = Control.MOUSE_FILTER_IGNORE
	parent.add_child(_death_overlay)
	_death_text = Label.new()
	_death_text.text = "YOU DIED"
	_death_text.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_death_text.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	_death_text.set_anchors_preset(Control.PRESET_FULL_RECT)
	_death_text.add_theme_font_size_override("font_size", 64)
	_death_text.add_theme_color_override("font_color", Color(0.7, 0.15, 0.1))
	_death_text.modulate.a = 0.0
	_death_overlay.add_child(_death_text)


func _build_damage_container(parent: Control) -> void:
	_damage_container = Control.new()
	_damage_container.set_anchors_preset(Control.PRESET_FULL_RECT)
	_damage_container.mouse_filter = Control.MOUSE_FILTER_IGNORE
	parent.add_child(_damage_container)


func _on_health_changed(current: float, maximum: float) -> void:
	_health_bar.max_value = maximum
	_health_bar.value = current


func _on_stamina_changed(current: float, maximum: float) -> void:
	_stamina_bar.max_value = maximum
	_stamina_bar.value = current


func _on_mana_changed(current: float, maximum: float) -> void:
	_mana_bar.max_value = maximum
	_mana_bar.value = current


func _on_souls_changed(amount: int) -> void:
	_souls_label.text = str(amount)


func _on_state_changed(new_state: GameManager.State) -> void:
	visible = new_state != GameManager.State.MENU


func _on_interaction_available(_target: Node3D, label: String) -> void:
	var key := SettingsManager.get_action_display_name("interact")
	_interact_label.text = "[" + key + "] " + label
	_interact_label.visible = true


func _on_interaction_cleared() -> void:
	_interact_label.visible = false


func _on_player_died() -> void:
	_death_overlay.visible = true
	var tween := create_tween()
	tween.tween_property(_death_overlay, "color", Color(0, 0, 0, 0.7), 1.5)
	tween.parallel().tween_property(_death_text, "modulate:a", 1.0, 1.0).set_delay(0.3)


func _on_player_respawned() -> void:
	_player_connected = false
	var tween := create_tween()
	tween.tween_property(_death_overlay, "color", Color(0, 0, 0, 0), 0.5)
	tween.parallel().tween_property(_death_text, "modulate:a", 0.0, 0.3)
	tween.tween_callback(func() -> void: _death_overlay.visible = false)


func _on_attack_hit(target: Node3D, dmg: float) -> void:
	if not SettingsManager.show_damage_numbers:
		return
	var cam := get_viewport().get_camera_3d()
	if not cam:
		return
	var world_pos := target.global_position + Vector3(0, 1.5, 0)
	if cam.is_position_behind(world_pos):
		return
	var screen_pos := cam.unproject_position(world_pos)
	var label := Label.new()
	label.text = str(int(dmg))
	label.position = screen_pos + Vector2(randf_range(-15, 15), -20)
	label.add_theme_font_size_override("font_size", 22)
	label.add_theme_color_override("font_color", Color(1.0, 0.9, 0.3))
	_damage_container.add_child(label)
	var t := create_tween()
	t.tween_property(label, "position:y", label.position.y - 40, 0.8)
	t.parallel().tween_property(label, "modulate:a", 0.0, 0.8).set_delay(0.3)
	t.tween_callback(label.queue_free)
