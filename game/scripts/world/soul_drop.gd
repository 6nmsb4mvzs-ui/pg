extends Area3D

var soul_amount: int = 0
var _collected: bool = false


func _ready() -> void:
	collision_layer = 0
	collision_mask = 2
	add_to_group("soul_drops")
	body_entered.connect(_on_body_entered)


func setup(amount: int, pos: Vector3) -> void:
	soul_amount = amount
	global_position = pos


func _on_body_entered(body: Node3D) -> void:
	if _collected:
		return
	if body == GameManager.player:
		_collected = true
		GameManager.add_souls(soul_amount)
		GameManager.dropped_souls_amount = 0
		queue_free()
