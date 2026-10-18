extends CharacterBody3D

@export var walk_speed: float = 4.0
@export var sprint_speed: float = 7.0
@export var dodge_speed: float = 10.0
@export var dodge_duration: float = 0.45
@export var dodge_stamina_cost: float = 25.0
@export var sprint_stamina_rate: float = 15.0
@export var gravity_force: float = 20.0
@export var attack_range: float = 3.0
@export var heavy_attack_range: float = 3.5
@export var attack_damage: float = 25.0
@export var heavy_attack_damage: float = 50.0
@export var attack_stamina_cost: float = 15.0
@export var heavy_attack_stamina_cost: float = 30.0
@export var attack_duration: float = 0.4
@export var heavy_attack_duration: float = 0.7
@export var attack_cooldown: float = 0.15
@export var heal_mana_cost: float = 30.0
@export var heal_amount: float = 40.0
@export var heal_duration: float = 0.8
@export var interaction_range: float = 2.5

enum PlayerState { IDLE, MOVING, SPRINTING, DODGING, ATTACKING, HEAVY_ATTACKING, HEALING, STAGGERED, DEAD }

var current_state: PlayerState = PlayerState.IDLE
var dodge_timer: float = 0.0
var dodge_direction: Vector3 = Vector3.ZERO
var attack_timer: float = 0.0
var cooldown_timer: float = 0.0
var heal_timer: float = 0.0
var stagger_timer: float = 0.0
var is_invincible: bool = false
var dodge_iframes_start: float = 0.05
var dodge_iframes_end: float = 0.35
var _interact_target: Node3D = null

@onready var camera: Camera3D = $Camera3D
@onready var stats: Node = $PlayerStats
@onready var attack_ray: RayCast3D = $Camera3D/AttackRay
@onready var interact_ray: RayCast3D = $Camera3D/InteractRay
@onready var weapon_pivot: Node3D = $Camera3D/WeaponPivot

signal interaction_available(target: Node3D, label: String)
signal interaction_cleared
signal attack_hit(target: Node3D, damage: float)
signal state_changed_to(new_state: PlayerState)


func _ready() -> void:
	Input.mouse_mode = Input.MOUSE_MODE_CAPTURED
	GameManager.player = self
	GameManager.state_changed.connect(_on_game_state_changed)
	if not GameManager.pending_player_data.is_empty():
		_apply_save_data(GameManager.pending_player_data)
		GameManager.pending_player_data.clear()
	_create_weapon_mesh()


func _unhandled_input(event: InputEvent) -> void:
	if GameManager.state != GameManager.State.PLAYING:
		return
	if event is InputEventMouseMotion and Input.mouse_mode == Input.MOUSE_MODE_CAPTURED:
		var sens: float = SettingsManager.mouse_sensitivity
		var y_mult: float = -1.0 if SettingsManager.invert_y else 1.0
		rotate_y(-event.relative.x * sens)
		camera.rotate_x(event.relative.y * sens * y_mult)
		camera.rotation.x = clampf(camera.rotation.x, -1.4, 1.4)


func _physics_process(delta: float) -> void:
	if GameManager.state != GameManager.State.PLAYING and GameManager.state != GameManager.State.DEAD:
		return
	if current_state == PlayerState.DEAD:
		return

	if not is_on_floor():
		velocity.y -= gravity_force * delta

	_update_timers(delta)
	_update_invincibility()
	_check_interaction()

	if _can_act():
		_handle_input(delta)

	_apply_movement(delta)
	move_and_slide()

	if Input.is_action_just_pressed("pause_menu"):
		GameManager.toggle_pause()


func _update_timers(delta: float) -> void:
	if dodge_timer > 0.0:
		dodge_timer -= delta
		if dodge_timer <= 0.0:
			_end_dodge()

	if attack_timer > 0.0:
		attack_timer -= delta
		if attack_timer <= 0.0:
			_end_attack()

	if cooldown_timer > 0.0:
		cooldown_timer -= delta

	if heal_timer > 0.0:
		heal_timer -= delta
		if heal_timer <= 0.0:
			_end_heal()

	if stagger_timer > 0.0:
		stagger_timer -= delta
		if stagger_timer <= 0.0:
			_set_state(PlayerState.IDLE)


func _update_invincibility() -> void:
	if current_state == PlayerState.DODGING:
		var elapsed := dodge_duration - dodge_timer
		is_invincible = elapsed >= dodge_iframes_start and elapsed <= dodge_iframes_end
	else:
		is_invincible = false


func _can_act() -> bool:
	return current_state in [PlayerState.IDLE, PlayerState.MOVING, PlayerState.SPRINTING]


func _handle_input(delta: float) -> void:
	if Input.is_action_just_pressed("dodge") and stats.use_stamina(dodge_stamina_cost):
		_start_dodge()
		return

	if Input.is_action_just_pressed("attack") and cooldown_timer <= 0.0 and stats.use_stamina(attack_stamina_cost):
		_start_attack(false)
		return

	if Input.is_action_just_pressed("heavy_attack") and cooldown_timer <= 0.0 and stats.use_stamina(heavy_attack_stamina_cost):
		_start_attack(true)
		return

	if Input.is_action_just_pressed("heal"):
		_try_heal()
		return

	if Input.is_action_just_pressed("interact") and _interact_target:
		if _interact_target.has_method("interact"):
			_interact_target.interact(self)
		return

	var input_dir := Input.get_vector("move_left", "move_right", "move_forward", "move_back")
	var direction := (transform.basis * Vector3(input_dir.x, 0, input_dir.y)).normalized()
	var is_sprint: bool = Input.is_action_pressed("sprint") and direction.length() > 0.1 and stats.stamina > 0

	if is_sprint:
		stats.use_stamina(sprint_stamina_rate * delta)
		_set_state(PlayerState.SPRINTING)
	elif direction.length() > 0.1:
		_set_state(PlayerState.MOVING)
	else:
		_set_state(PlayerState.IDLE)

	var speed := sprint_speed if is_sprint else walk_speed
	if direction.length() > 0.1:
		velocity.x = direction.x * speed
		velocity.z = direction.z * speed
	else:
		velocity.x = move_toward(velocity.x, 0, walk_speed * delta * 12.0)
		velocity.z = move_toward(velocity.z, 0, walk_speed * delta * 12.0)


func _apply_movement(delta: float) -> void:
	if current_state == PlayerState.DODGING:
		var t := 1.0 - (dodge_timer / dodge_duration)
		var speed := dodge_speed * (1.0 - t * t)
		velocity.x = dodge_direction.x * speed
		velocity.z = dodge_direction.z * speed
	elif current_state in [PlayerState.ATTACKING, PlayerState.HEAVY_ATTACKING, PlayerState.HEALING]:
		velocity.x = move_toward(velocity.x, 0, 20.0 * delta)
		velocity.z = move_toward(velocity.z, 0, 20.0 * delta)
	elif current_state == PlayerState.STAGGERED:
		velocity.x = move_toward(velocity.x, 0, 15.0 * delta)
		velocity.z = move_toward(velocity.z, 0, 15.0 * delta)


func _start_dodge() -> void:
	var input_dir := Input.get_vector("move_left", "move_right", "move_forward", "move_back")
	var dir := (transform.basis * Vector3(input_dir.x, 0, input_dir.y)).normalized()
	if dir.length() < 0.1:
		dir = -transform.basis.z
	dodge_direction = dir.normalized()
	dodge_timer = dodge_duration
	_set_state(PlayerState.DODGING)


func _end_dodge() -> void:
	_set_state(PlayerState.IDLE)


func _start_attack(heavy: bool) -> void:
	if heavy:
		attack_timer = heavy_attack_duration
		_set_state(PlayerState.HEAVY_ATTACKING)
	else:
		attack_timer = attack_duration
		_set_state(PlayerState.ATTACKING)
	_perform_attack_hit(heavy)


func _perform_attack_hit(heavy: bool) -> void:
	var range_val := heavy_attack_range if heavy else attack_range
	var damage := heavy_attack_damage if heavy else attack_damage
	damage *= stats.get_damage_multiplier()
	attack_ray.target_position = Vector3(0, 0, -range_val)
	attack_ray.force_raycast_update()
	if attack_ray.is_colliding():
		var target := attack_ray.get_collider()
		if target and target.has_method("take_damage"):
			var hit_pos := attack_ray.get_collision_point()
			target.take_damage(damage, hit_pos, -transform.basis.z, heavy)
			attack_hit.emit(target, damage)


func _end_attack() -> void:
	cooldown_timer = attack_cooldown
	_set_state(PlayerState.IDLE)


func _try_heal() -> void:
	if stats.mana >= heal_mana_cost and stats.health < stats.max_health:
		stats.use_mana(heal_mana_cost)
		heal_timer = heal_duration
		_set_state(PlayerState.HEALING)


func _end_heal() -> void:
	stats.heal(heal_amount)
	_set_state(PlayerState.IDLE)


func _check_interaction() -> void:
	if not interact_ray:
		return
	interact_ray.target_position = Vector3(0, 0, -interaction_range)
	interact_ray.force_raycast_update()
	if interact_ray.is_colliding():
		var target := interact_ray.get_collider()
		if target and target.has_method("get_interaction_label"):
			if target != _interact_target:
				_interact_target = target
				interaction_available.emit(target, target.get_interaction_label())
			return
	if _interact_target:
		_interact_target = null
		interaction_cleared.emit()


func take_damage(amount: float, from_pos: Vector3, _from_dir: Vector3, _heavy: bool = false) -> void:
	if is_invincible or current_state == PlayerState.DEAD:
		return
	var actual: float = stats.apply_damage(amount)
	if stats.health <= 0:
		_die()
	elif actual > stats.max_health * 0.25:
		stagger_timer = 0.3
		_set_state(PlayerState.STAGGERED)


func _die() -> void:
	_set_state(PlayerState.DEAD)
	GameManager.on_player_death(global_position)


func respawn(pos: Vector3) -> void:
	global_position = pos
	velocity = Vector3.ZERO
	stats.full_restore()
	is_invincible = false
	dodge_timer = 0.0
	attack_timer = 0.0
	cooldown_timer = 0.0
	heal_timer = 0.0
	stagger_timer = 0.0
	_set_state(PlayerState.IDLE)


func full_restore() -> void:
	stats.full_restore()


func _set_state(new_state: PlayerState) -> void:
	if current_state != new_state:
		current_state = new_state
		state_changed_to.emit(new_state)


func _on_game_state_changed(new_state: GameManager.State) -> void:
	if new_state == GameManager.State.PLAYING:
		Input.mouse_mode = Input.MOUSE_MODE_CAPTURED
	elif new_state in [GameManager.State.PAUSED, GameManager.State.MENU]:
		Input.mouse_mode = Input.MOUSE_MODE_VISIBLE


func get_save_data() -> Dictionary:
	return {
		"position": [global_position.x, global_position.y, global_position.z],
		"rotation_y": rotation.y,
		"stats": stats.get_save_data() if stats.has_method("get_save_data") else {},
	}


func _create_weapon_mesh() -> void:
	var blade_mat := StandardMaterial3D.new()
	blade_mat.albedo_color = Color(0.5, 0.48, 0.52)
	blade_mat.metallic = 0.9
	blade_mat.roughness = 0.15

	var edge_mat := StandardMaterial3D.new()
	edge_mat.albedo_color = Color(0.8, 0.85, 0.9)
	edge_mat.metallic = 1.0
	edge_mat.roughness = 0.05
	edge_mat.emission_enabled = true
	edge_mat.emission = Color(0.6, 0.65, 0.75)
	edge_mat.emission_energy_multiplier = 0.4

	var guard_mat := StandardMaterial3D.new()
	guard_mat.albedo_color = Color(0.3, 0.25, 0.18)
	guard_mat.metallic = 0.7
	guard_mat.roughness = 0.4

	var grip_mat := StandardMaterial3D.new()
	grip_mat.albedo_color = Color(0.15, 0.08, 0.05)
	grip_mat.roughness = 0.9

	var blade := MeshInstance3D.new()
	var blade_mesh := BoxMesh.new()
	blade_mesh.size = Vector3(0.06, 0.9, 0.018)
	blade_mesh.material = blade_mat
	blade.mesh = blade_mesh
	weapon_pivot.add_child(blade)

	var edge_l := MeshInstance3D.new()
	var el_mesh := BoxMesh.new()
	el_mesh.size = Vector3(0.003, 0.82, 0.02)
	el_mesh.material = edge_mat
	edge_l.mesh = el_mesh
	edge_l.position = Vector3(-0.032, 0, 0)
	weapon_pivot.add_child(edge_l)

	var edge_r := MeshInstance3D.new()
	var er_mesh := BoxMesh.new()
	er_mesh.size = Vector3(0.003, 0.82, 0.02)
	er_mesh.material = edge_mat
	edge_r.mesh = er_mesh
	edge_r.position = Vector3(0.032, 0, 0)
	weapon_pivot.add_child(edge_r)

	var guard := MeshInstance3D.new()
	var guard_mesh := BoxMesh.new()
	guard_mesh.size = Vector3(0.2, 0.03, 0.06)
	guard_mesh.material = guard_mat
	guard.mesh = guard_mesh
	guard.position = Vector3(0, -0.48, 0)
	weapon_pivot.add_child(guard)

	var grip := MeshInstance3D.new()
	var grip_mesh := BoxMesh.new()
	grip_mesh.size = Vector3(0.035, 0.25, 0.035)
	grip_mesh.material = grip_mat
	grip.mesh = grip_mesh
	grip.position = Vector3(0, -0.62, 0)
	weapon_pivot.add_child(grip)

	var pommel := MeshInstance3D.new()
	var pommel_mesh := SphereMesh.new()
	pommel_mesh.radius = 0.025
	pommel_mesh.height = 0.05
	pommel_mesh.material = guard_mat
	pommel.mesh = pommel_mesh
	pommel.position = Vector3(0, -0.77, 0)
	weapon_pivot.add_child(pommel)


func _apply_save_data(data: Dictionary) -> void:
	var pos: Array = data.get("position", [0, 1, 0])
	if pos.size() >= 3:
		global_position = Vector3(pos[0], pos[1], pos[2])
	rotation.y = data.get("rotation_y", 0.0)
	if stats.has_method("load_save_data") and data.has("stats"):
		stats.load_save_data(data["stats"])
