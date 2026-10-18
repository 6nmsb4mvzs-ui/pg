extends Node3D

@export var scene_id: String = ""

var _soul_drop_spawned: bool = false


func _ready() -> void:
	var path := scene_file_path
	if not path.is_empty():
		GameManager.current_scene_path = path
	elif not scene_id.is_empty():
		GameManager.current_scene_path = scene_id
	GameManager.player_respawned.connect(_on_player_respawned)
	if GameManager.dropped_souls_amount > 0 and GameManager.dropped_souls_scene == GameManager.current_scene_path:
		_spawn_soul_drop(GameManager.dropped_souls_position, GameManager.dropped_souls_amount)
	if not GameManager.pending_player_data.is_empty():
		call_deferred("_apply_pending_player_data")
	if GameManager.state == GameManager.State.LOADING:
		GameManager.change_state(GameManager.State.PLAYING)
	_create_atmosphere()


func _apply_pending_player_data() -> void:
	var player := GameManager.player
	if player and player.has_method("_apply_save_data"):
		player._apply_save_data(GameManager.pending_player_data)
		GameManager.pending_player_data.clear()


func _on_player_respawned() -> void:
	_soul_drop_spawned = false
	if GameManager.dropped_souls_amount > 0 and GameManager.dropped_souls_scene == GameManager.current_scene_path:
		_spawn_soul_drop(GameManager.dropped_souls_position, GameManager.dropped_souls_amount)


func _spawn_soul_drop(pos: Vector3, amount: int) -> void:
	if _soul_drop_spawned:
		return
	_soul_drop_spawned = true
	var drop := Area3D.new()
	drop.collision_layer = 0
	drop.collision_mask = 2
	drop.set_script(load("res://scripts/world/soul_drop.gd"))
	var col := CollisionShape3D.new()
	var sphere := SphereShape3D.new()
	sphere.radius = 1.5
	col.shape = sphere
	drop.add_child(col)
	var mesh_inst := MeshInstance3D.new()
	var sphere_mesh := SphereMesh.new()
	sphere_mesh.radius = 0.25
	sphere_mesh.height = 0.5
	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(0.9, 0.8, 0.3)
	mat.emission_enabled = true
	mat.emission = Color(0.9, 0.8, 0.3)
	mat.emission_energy_multiplier = 4.0
	sphere_mesh.material = mat
	mesh_inst.mesh = sphere_mesh
	drop.add_child(mesh_inst)
	var light := OmniLight3D.new()
	light.light_color = Color(0.9, 0.8, 0.3)
	light.light_energy = 2.0
	light.omni_range = 5.0
	drop.add_child(light)
	add_child(drop)
	drop.setup(amount, pos)


func _create_atmosphere() -> void:
	var ash := CPUParticles3D.new()
	ash.amount = 80
	ash.lifetime = 10.0
	ash.local_coords = false
	ash.emission_shape = CPUParticles3D.EMISSION_SHAPE_BOX
	ash.emission_box_extents = Vector3(22, 6, 22)
	ash.direction = Vector3(-0.3, -0.15, 0.1)
	ash.spread = 70.0
	ash.initial_velocity_min = 0.08
	ash.initial_velocity_max = 0.35
	ash.gravity = Vector3(0, -0.03, 0)
	ash.scale_amount_min = 0.008
	ash.scale_amount_max = 0.02
	var mesh := SphereMesh.new()
	mesh.radius = 0.5
	mesh.height = 1.0
	mesh.radial_segments = 4
	mesh.rings = 2
	var mat := StandardMaterial3D.new()
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.albedo_color = Color(0.55, 0.5, 0.45, 0.25)
	mat.billboard_mode = BaseMaterial3D.BILLBOARD_PARTICLES
	mesh.material = mat
	ash.mesh = mesh
	ash.position = Vector3(0, 3, 0)
	add_child(ash)
