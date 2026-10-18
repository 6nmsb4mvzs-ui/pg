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
	_create_body_mesh()
	_create_eyes()


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


func _create_body_mesh() -> void:
	var armor_mat := StandardMaterial3D.new()
	armor_mat.albedo_color = Color(0.12, 0.1, 0.09)
	armor_mat.metallic = 0.7
	armor_mat.roughness = 0.35

	var cloth_mat := StandardMaterial3D.new()
	cloth_mat.albedo_color = Color(0.06, 0.05, 0.05)
	cloth_mat.roughness = 0.95

	var skin_mat := StandardMaterial3D.new()
	skin_mat.albedo_color = Color(0.35, 0.3, 0.27)
	skin_mat.roughness = 0.85

	var weapon_mat := StandardMaterial3D.new()
	weapon_mat.albedo_color = Color(0.45, 0.42, 0.4)
	weapon_mat.metallic = 0.85
	weapon_mat.roughness = 0.2

	var torso := MeshInstance3D.new()
	var torso_mesh := BoxMesh.new()
	torso_mesh.size = Vector3(0.55, 0.7, 0.3)
	torso_mesh.material = armor_mat
	torso.mesh = torso_mesh
	torso.position = Vector3(0, 1.15, 0)
	add_child(torso)

	var waist := MeshInstance3D.new()
	var waist_mesh := BoxMesh.new()
	waist_mesh.size = Vector3(0.35, 0.25, 0.25)
	waist_mesh.material = cloth_mat
	waist.mesh = waist_mesh
	waist.position = Vector3(0, 0.7, 0)
	add_child(waist)

	var leg_l := MeshInstance3D.new()
	var ll_mesh := BoxMesh.new()
	ll_mesh.size = Vector3(0.16, 0.55, 0.18)
	ll_mesh.material = armor_mat
	leg_l.mesh = ll_mesh
	leg_l.position = Vector3(-0.1, 0.3, 0)
	add_child(leg_l)

	var leg_r := MeshInstance3D.new()
	var lr_mesh := BoxMesh.new()
	lr_mesh.size = Vector3(0.16, 0.55, 0.18)
	lr_mesh.material = armor_mat
	leg_r.mesh = lr_mesh
	leg_r.position = Vector3(0.1, 0.3, 0)
	add_child(leg_r)

	var arm_l := MeshInstance3D.new()
	var al_mesh := BoxMesh.new()
	al_mesh.size = Vector3(0.14, 0.6, 0.14)
	al_mesh.material = cloth_mat
	arm_l.mesh = al_mesh
	arm_l.position = Vector3(-0.35, 1.0, 0)
	add_child(arm_l)

	var arm_r := MeshInstance3D.new()
	var ar_mesh := BoxMesh.new()
	ar_mesh.size = Vector3(0.14, 0.6, 0.14)
	ar_mesh.material = armor_mat
	arm_r.mesh = ar_mesh
	arm_r.position = Vector3(0.35, 1.0, 0)
	add_child(arm_r)

	var pauldron_l := MeshInstance3D.new()
	var pl_mesh := BoxMesh.new()
	pl_mesh.size = Vector3(0.22, 0.1, 0.22)
	pl_mesh.material = armor_mat
	pauldron_l.mesh = pl_mesh
	pauldron_l.position = Vector3(-0.35, 1.45, 0)
	add_child(pauldron_l)

	var pauldron_r := MeshInstance3D.new()
	var pr_mesh := BoxMesh.new()
	pr_mesh.size = Vector3(0.22, 0.1, 0.22)
	pr_mesh.material = armor_mat
	pauldron_r.mesh = pr_mesh
	pauldron_r.position = Vector3(0.35, 1.45, 0)
	add_child(pauldron_r)

	var head := MeshInstance3D.new()
	var head_mesh := SphereMesh.new()
	head_mesh.radius = 0.15
	head_mesh.height = 0.3
	head_mesh.material = skin_mat
	head.mesh = head_mesh
	head.position = Vector3(0, 1.7, 0)
	add_child(head)

	var hood := MeshInstance3D.new()
	var hood_mesh := BoxMesh.new()
	hood_mesh.size = Vector3(0.3, 0.25, 0.32)
	hood_mesh.material = cloth_mat
	hood.mesh = hood_mesh
	hood.position = Vector3(0, 1.75, 0.02)
	add_child(hood)

	var cape := MeshInstance3D.new()
	var cape_mesh := BoxMesh.new()
	cape_mesh.size = Vector3(0.45, 0.9, 0.02)
	cape_mesh.material = cloth_mat
	cape.mesh = cape_mesh
	cape.position = Vector3(0, 0.95, 0.17)
	add_child(cape)

	var blade := MeshInstance3D.new()
	var blade_mesh := BoxMesh.new()
	blade_mesh.size = Vector3(0.04, 0.8, 0.015)
	blade_mesh.material = weapon_mat
	blade.mesh = blade_mesh
	blade.position = Vector3(0.35, 0.7, -0.25)
	add_child(blade)

	var guard := MeshInstance3D.new()
	var guard_mesh := BoxMesh.new()
	guard_mesh.size = Vector3(0.14, 0.025, 0.04)
	guard_mesh.material = weapon_mat
	guard.mesh = guard_mesh
	guard.position = Vector3(0.35, 0.28, -0.25)
	add_child(guard)


func _create_eyes() -> void:
	var eye_mat := StandardMaterial3D.new()
	eye_mat.albedo_color = Color(1, 0.12, 0.03)
	eye_mat.emission_enabled = true
	eye_mat.emission = Color(1, 0.12, 0.03)
	eye_mat.emission_energy_multiplier = 6.0

	var left_eye := MeshInstance3D.new()
	var le_mesh := BoxMesh.new()
	le_mesh.size = Vector3(0.06, 0.04, 0.06)
	le_mesh.material = eye_mat
	left_eye.mesh = le_mesh
	left_eye.position = Vector3(-0.08, 2.02, -0.18)
	add_child(left_eye)

	var right_eye := MeshInstance3D.new()
	var re_mesh := BoxMesh.new()
	re_mesh.size = Vector3(0.06, 0.04, 0.06)
	re_mesh.material = eye_mat
	right_eye.mesh = re_mesh
	right_eye.position = Vector3(0.08, 2.02, -0.18)
	add_child(right_eye)
