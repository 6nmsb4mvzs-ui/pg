extends Control


func _ready() -> void:
	var bg := ColorRect.new()
	bg.color = Color(0.02, 0.02, 0.03, 1)
	bg.set_anchors_preset(PRESET_FULL_RECT)
	add_child(bg)

	var center := VBoxContainer.new()
	center.set_anchors_preset(PRESET_CENTER)
	center.offset_left = -150
	center.offset_right = 150
	center.offset_top = -200
	center.offset_bottom = 200
	center.alignment = BoxContainer.ALIGNMENT_CENTER
	center.add_theme_constant_override("separation", 12)
	add_child(center)

	var title := Label.new()
	title.text = "ASHVALE"
	title.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	title.add_theme_font_size_override("font_size", 72)
	title.add_theme_color_override("font_color", Color(0.85, 0.7, 0.35))
	center.add_child(title)

	var subtitle := Label.new()
	subtitle.text = "A land consumed by ash and silence"
	subtitle.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	subtitle.add_theme_font_size_override("font_size", 14)
	subtitle.add_theme_color_override("font_color", Color(0.45, 0.4, 0.3))
	center.add_child(subtitle)

	var spacer := Control.new()
	spacer.custom_minimum_size = Vector2(0, 40)
	center.add_child(spacer)

	_add_button(center, "New Game", _on_new_game)
	var cont := _add_button(center, "Continue", _on_continue)
	cont.disabled = not SaveManager.has_save()
	_add_button(center, "Settings", _on_settings)
	_add_button(center, "Quit", _on_quit)

	var version := Label.new()
	version.text = "v0.1.0"
	version.set_anchors_preset(PRESET_BOTTOM_RIGHT)
	version.offset_left = -100
	version.offset_top = -30
	version.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	version.add_theme_font_size_override("font_size", 12)
	version.add_theme_color_override("font_color", Color(0.3, 0.3, 0.3))
	add_child(version)


func _add_button(parent: Control, text: String, callback: Callable) -> Button:
	var btn := Button.new()
	btn.text = text
	btn.custom_minimum_size = Vector2(260, 44)
	btn.add_theme_font_size_override("font_size", 20)
	btn.add_theme_color_override("font_color", Color(0.8, 0.75, 0.65))
	btn.add_theme_color_override("font_hover_color", Color(0.95, 0.85, 0.5))
	btn.add_theme_color_override("font_disabled_color", Color(0.3, 0.3, 0.3))
	var normal := StyleBoxFlat.new()
	normal.bg_color = Color(0.08, 0.07, 0.06, 0.9)
	normal.border_width_bottom = 1
	normal.border_width_top = 1
	normal.border_width_left = 1
	normal.border_width_right = 1
	normal.border_color = Color(0.3, 0.25, 0.15, 0.5)
	normal.set_corner_radius_all(3)
	normal.set_content_margin_all(10)
	btn.add_theme_stylebox_override("normal", normal)
	var hover := normal.duplicate() as StyleBoxFlat
	hover.bg_color = Color(0.12, 0.1, 0.08, 0.95)
	hover.border_color = Color(0.5, 0.4, 0.2, 0.7)
	btn.add_theme_stylebox_override("hover", hover)
	var pressed_box := normal.duplicate() as StyleBoxFlat
	pressed_box.bg_color = Color(0.05, 0.04, 0.03, 0.95)
	btn.add_theme_stylebox_override("pressed", pressed_box)
	var disabled_box := normal.duplicate() as StyleBoxFlat
	disabled_box.bg_color = Color(0.05, 0.05, 0.05, 0.5)
	disabled_box.border_color = Color(0.15, 0.15, 0.15, 0.3)
	btn.add_theme_stylebox_override("disabled", disabled_box)
	btn.pressed.connect(callback)
	parent.add_child(btn)
	return btn


func _on_new_game() -> void:
	GameManager.start_new_game()


func _on_continue() -> void:
	GameManager.continue_game()


func _on_settings() -> void:
	pass


func _on_quit() -> void:
	get_tree().quit()
