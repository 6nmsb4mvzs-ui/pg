extends StaticBody3D

@export var rest_point_id: String = "rest_default"
@export var rest_position_offset: Vector3 = Vector3(0, 0, 1.5)

var _is_active: bool = false
var _light: OmniLight3D = null
var _base_energy: float = 5.0

signal rested


func _ready() -> void:
	add_to_group("rest_points")
	collision_layer = 8
	_light = get_node_or_null("BonfireLight")
	if _light:
		_base_energy = _light.light_energy
	_create_fire_particles()
	_create_ember_particles()
	_create_smoke_particles()


func _process(_delta: float) -> void:
	if _light:
		var t := float(Time.get_ticks_msec())
		_light.light_energy = _base_energy + sin(t * 0.008) * 0.5 + sin(t * 0.013) * 0.3 + sin(t * 0.021) * 0.15


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


func _create_fire_particles() -> void:
	var fire := CPUParticles3D.new()
	fire.amount = 35
	fire.lifetime = 0.7
	fire.local_coords = false
	fire.emission_shape = CPUParticles3D.EMISSION_SHAPE_SPHERE
	fire.emission_sphere_radius = 0.2
	fire.direction = Vector3(0, 1, 0)
	fire.spread = 18.0
	fire.initial_velocity_min = 1.0
	fire.initial_velocity_max = 2.5
	fire.gravity = Vector3(0, 0.5, 0)
	fire.scale_amount_min = 0.03
	fire.scale_amount_max = 0.09
	var mesh := SphereMesh.new()
	mesh.radius = 0.5
	mesh.height = 1.0
	mesh.radial_segments = 4
	mesh.rings = 2
	var mat := StandardMaterial3D.new()
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.albedo_color = Color(1, 0.45, 0.05, 0.85)
	mat.emission_enabled = true
	mat.emission = Color(1, 0.45, 0.05)
	mat.emission_energy_multiplier = 4.0
	mat.billboard_mode = BaseMaterial3D.BILLBOARD_PARTICLES
	mesh.material = mat
	fire.mesh = mesh
	fire.position = Vector3(0, 0.3, 0)
	add_child(fire)


func _create_ember_particles() -> void:
	var embers := CPUParticles3D.new()
	embers.amount = 25
	embers.lifetime = 2.5
	embers.local_coords = false
	embers.emission_shape = CPUParticles3D.EMISSION_SHAPE_SPHERE
	embers.emission_sphere_radius = 0.35
	embers.direction = Vector3(0, 1, 0)
	embers.spread = 50.0
	embers.initial_velocity_min = 0.4
	embers.initial_velocity_max = 1.8
	embers.gravity = Vector3(0, -0.2, 0)
	embers.scale_amount_min = 0.008
	embers.scale_amount_max = 0.025
	var mesh := SphereMesh.new()
	mesh.radius = 0.5
	mesh.height = 1.0
	mesh.radial_segments = 4
	mesh.rings = 2
	var mat := StandardMaterial3D.new()
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.albedo_color = Color(1, 0.6, 0.15, 0.7)
	mat.emission_enabled = true
	mat.emission = Color(1, 0.55, 0.1)
	mat.emission_energy_multiplier = 2.5
	mat.billboard_mode = BaseMaterial3D.BILLBOARD_PARTICLES
	mesh.material = mat
	embers.mesh = mesh
	embers.position = Vector3(0, 0.4, 0)
	add_child(embers)


func _create_smoke_particles() -> void:
	var smoke := CPUParticles3D.new()
	smoke.amount = 12
	smoke.lifetime = 3.0
	smoke.local_coords = false
	smoke.emission_shape = CPUParticles3D.EMISSION_SHAPE_SPHERE
	smoke.emission_sphere_radius = 0.15
	smoke.direction = Vector3(0, 1, 0)
	smoke.spread = 12.0
	smoke.initial_velocity_min = 0.3
	smoke.initial_velocity_max = 0.8
	smoke.gravity = Vector3(0, 0.3, 0)
	smoke.scale_amount_min = 0.08
	smoke.scale_amount_max = 0.2
	var mesh := SphereMesh.new()
	mesh.radius = 0.5
	mesh.height = 1.0
	mesh.radial_segments = 4
	mesh.rings = 2
	var mat := StandardMaterial3D.new()
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.albedo_color = Color(0.2, 0.18, 0.16, 0.15)
	mat.billboard_mode = BaseMaterial3D.BILLBOARD_PARTICLES
	mesh.material = mat
	smoke.mesh = mesh
	smoke.position = Vector3(0, 0.6, 0)
	add_child(smoke)
