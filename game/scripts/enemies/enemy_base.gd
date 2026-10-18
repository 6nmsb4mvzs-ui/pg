extends CharacterBody3D
class_name EnemyBase

@export var enemy_id: String = ""
@export var max_health: float = 80.0
@export var damage: float = 20.0
@export var heavy_damage: float = 35.0
@export var attack_range: float = 2.0
@export var detection_range: float = 15.0
@export var move_speed: float = 3.0
@export var chase_speed: float = 4.5
@export var attack_cooldown: float = 1.5
@export var stagger_threshold: float = 30.0
@export var souls_reward: int = 50
@export var is_unique: bool = false
@export var gravity_force: float = 20.0
@export var patrol_radius: float = 8.0
@export var leash_range: float = 25.0

enum AIState { IDLE, PATROL, CHASE, ATTACK, RECOVER, STAGGERED, DEAD }

var health: float = 80.0
var ai_state: AIState = AIState.IDLE
var _target: CharacterBody3D = null
var _attack_timer: float = 0.0
var _stagger_timer: float = 0.0
var _recover_timer: float = 0.0
var _accumulated_damage: float = 0.0
var _stagger_window: float = 0.0
var _spawn_position: Vector3 = Vector3.ZERO
var _patrol_target: Vector3 = Vector3.ZERO
var _patrol_wait: float = 0.0
var _is_dead: bool = false

signal took_damage(amount: float, remaining: float)
signal died


func _ready() -> void:
	add_to_group("enemies")
	health = max_health
	_spawn_position = global_position
	_patrol_target = _spawn_position
	if is_unique and GameManager.is_unique_defeated(enemy_id):
		queue_free()


func _physics_process(delta: float) -> void:
	if _is_dead:
		return
	if not is_on_floor():
		velocity.y -= gravity_force * delta
	_update_timers(delta)
	_update_ai(delta)
	move_and_slide()


func _update_timers(delta: float) -> void:
	if _attack_timer > 0.0:
		_attack_timer -= delta
	if _stagger_timer > 0.0:
		_stagger_timer -= delta
		if _stagger_timer <= 0.0:
			ai_state = AIState.RECOVER
			_recover_timer = 0.5
	if _recover_timer > 0.0:
		_recover_timer -= delta
		if _recover_timer <= 0.0 and ai_state == AIState.RECOVER:
			ai_state = AIState.CHASE if _target else AIState.IDLE
	if _stagger_window > 0.0:
		_stagger_window -= delta
		if _stagger_window <= 0.0:
			_accumulated_damage = 0.0


func _update_ai(delta: float) -> void:
	if ai_state in [AIState.STAGGERED, AIState.RECOVER, AIState.DEAD]:
		velocity.x = 0.0
		velocity.z = 0.0
		return

	_find_target()

	if _target and ai_state == AIState.CHASE:
		var dist_to_leash := global_position.distance_to(_spawn_position)
		if dist_to_leash > leash_range:
			_target = null
			ai_state = AIState.PATROL
			return

	match ai_state:
		AIState.IDLE:
			velocity.x = 0.0
			velocity.z = 0.0
			if _target:
				ai_state = AIState.CHASE
			else:
				_patrol_wait -= delta
				if _patrol_wait <= 0.0:
					ai_state = AIState.PATROL
					_pick_patrol_target()

		AIState.PATROL:
			if _target:
				ai_state = AIState.CHASE
				return
			var dir := (_patrol_target - global_position)
			dir.y = 0
			if dir.length() < 1.0:
				ai_state = AIState.IDLE
				_patrol_wait = randf_range(2.0, 5.0)
			else:
				dir = dir.normalized()
				velocity.x = dir.x * move_speed
				velocity.z = dir.z * move_speed
				_face_direction(dir)

		AIState.CHASE:
			if not _target or not is_instance_valid(_target):
				_target = null
				ai_state = AIState.PATROL
				return
			var to_target := _target.global_position - global_position
			to_target.y = 0
			var dist := to_target.length()
			if dist <= attack_range and _attack_timer <= 0.0:
				_start_attack()
			elif dist > detection_range * 1.5:
				_target = null
				ai_state = AIState.PATROL
			else:
				var dir := to_target.normalized()
				velocity.x = dir.x * chase_speed
				velocity.z = dir.z * chase_speed
				_face_direction(dir)

		AIState.ATTACK:
			velocity.x = 0.0
			velocity.z = 0.0


func _find_target() -> void:
	if _target and is_instance_valid(_target):
		return
	_target = null
	var player := GameManager.player
	if player and is_instance_valid(player):
		var dist := global_position.distance_to(player.global_position)
		if dist <= detection_range:
			_target = player


func _start_attack() -> void:
	ai_state = AIState.ATTACK
	_attack_timer = attack_cooldown
	_do_attack_damage()
	await get_tree().create_timer(0.5).timeout
	if not _is_dead and ai_state == AIState.ATTACK:
		ai_state = AIState.CHASE


func _do_attack_damage() -> void:
	if not _target or not is_instance_valid(_target):
		return
	var dist := global_position.distance_to(_target.global_position)
	if dist > attack_range * 1.3:
		return
	if _target.has_method("take_damage"):
		var dir := (_target.global_position - global_position).normalized()
		_target.take_damage(damage, global_position, dir, false)


func take_damage(amount: float, hit_pos: Vector3, from_dir: Vector3, heavy: bool = false) -> void:
	if _is_dead:
		return
	health -= amount
	_accumulated_damage += amount
	_stagger_window = 1.0
	took_damage.emit(amount, health)

	if health <= 0:
		_die()
		return

	if _accumulated_damage >= stagger_threshold or heavy:
		_accumulated_damage = 0.0
		ai_state = AIState.STAGGERED
		_stagger_timer = 0.8
		velocity.x = from_dir.x * 2.0
		velocity.z = from_dir.z * 2.0

	if not _target:
		_target = GameManager.player
		ai_state = AIState.CHASE


func _die() -> void:
	_is_dead = true
	ai_state = AIState.DEAD
	velocity = Vector3.ZERO
	GameManager.add_souls(souls_reward)
	if is_unique:
		GameManager.defeat_unique(enemy_id)
	died.emit()
	var tween := create_tween()
	tween.tween_property(self, "scale", Vector3(1, 0.01, 1), 1.5)
	tween.tween_callback(queue_free)


func reset_to_spawn() -> void:
	if is_unique and _is_dead:
		return
	_is_dead = false
	health = max_health
	ai_state = AIState.IDLE
	_target = null
	_attack_timer = 0.0
	_accumulated_damage = 0.0
	global_position = _spawn_position
	scale = Vector3.ONE
	velocity = Vector3.ZERO


func _pick_patrol_target() -> void:
	var angle := randf() * TAU
	var dist := randf_range(2.0, patrol_radius)
	_patrol_target = _spawn_position + Vector3(cos(angle) * dist, 0, sin(angle) * dist)


func _face_direction(dir: Vector3) -> void:
	if dir.length() > 0.01:
		var target_rot := atan2(dir.x, dir.z)
		rotation.y = lerp_angle(rotation.y, target_rot, 0.1)


func get_interaction_label() -> String:
	return ""
