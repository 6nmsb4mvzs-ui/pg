extends StaticBody3D

@export var rest_point_id: String = "rest_default"
@export var rest_position_offset: Vector3 = Vector3(0, 0, 1.5)

var _is_active: bool = false

signal rested


func _ready() -> void:
	add_to_group("rest_points")
	collision_layer = 8


func interact(by: Node) -> void:
	if _is_active:
		return
	_is_active = true
	var rest_pos := global_position + rest_position_offset
	GameManager.rest_at_point(rest_point_id, rest_pos)
	rested.emit()
	_is_active = false


func get_interaction_label() -> String:
	return "Rest"
