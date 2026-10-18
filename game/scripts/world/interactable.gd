extends StaticBody3D
class_name Interactable

@export var interaction_label: String = "Interact"
@export var one_shot: bool = false

var _used: bool = false

signal interacted(by: Node)


func _ready() -> void:
	add_to_group("interactables")
	collision_layer = 8  # layer 4: interactables


func interact(by: Node) -> void:
	if one_shot and _used:
		return
	_used = true
	interacted.emit(by)
	_on_interact(by)


func _on_interact(_by: Node) -> void:
	pass


func get_interaction_label() -> String:
	if one_shot and _used:
		return ""
	return interaction_label
