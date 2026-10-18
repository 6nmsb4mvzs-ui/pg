extends Node

enum State { MENU, PLAYING, PAUSED, DEAD, LOADING, RESTING }

var state: State = State.MENU
var souls: int = 0
var dropped_souls_position: Vector3 = Vector3.ZERO
var dropped_souls_amount: int = 0
var dropped_souls_scene: String = ""
var current_scene_path: String = ""

var last_rest_point_id: String = ""
var last_rest_point_scene: String = ""
var last_rest_point_position: Vector3 = Vector3.ZERO

var defeated_bosses: Array = []
var defeated_uniques: Array = []

var player: CharacterBody3D = null
var pending_player_data: Dictionary = {}

var _death_timer: float = 0.0
const DEATH_DELAY: float = 2.5

signal state_changed(new_state: State)
signal souls_changed(amount: int)
signal player_died
signal player_respawned


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS


func _process(delta: float) -> void:
	if state == State.DEAD:
		_death_timer -= delta
		if _death_timer <= 0.0:
			_do_respawn()


func change_state(new_state: State) -> void:
	state = new_state
	state_changed.emit(new_state)
	match new_state:
		State.PAUSED:
			get_tree().paused = true
		State.PLAYING, State.DEAD:
			get_tree().paused = false
		State.RESTING:
			get_tree().paused = false


func toggle_pause() -> void:
	if state == State.PLAYING:
		change_state(State.PAUSED)
	elif state == State.PAUSED:
		change_state(State.PLAYING)


func add_souls(amount: int) -> void:
	souls += amount
	souls_changed.emit(souls)


func spend_souls(amount: int) -> bool:
	if souls >= amount:
		souls -= amount
		souls_changed.emit(souls)
		return true
	return false


func on_player_death(death_position: Vector3) -> void:
	if state == State.DEAD:
		return
	for drop in get_tree().get_nodes_in_group("soul_drops"):
		drop.queue_free()
	dropped_souls_position = death_position
	dropped_souls_amount = souls
	dropped_souls_scene = current_scene_path
	souls = 0
	souls_changed.emit(souls)
	change_state(State.DEAD)
	_death_timer = DEATH_DELAY
	player_died.emit()


func _do_respawn() -> void:
	if last_rest_point_scene.is_empty():
		last_rest_point_scene = "res://scenes/world/test_arena.tscn"
		last_rest_point_position = Vector3(0, 1, 0)
	if last_rest_point_scene != current_scene_path:
		change_state(State.LOADING)
		get_tree().change_scene_to_file(last_rest_point_scene)
		await get_tree().tree_changed
		current_scene_path = last_rest_point_scene
	change_state(State.PLAYING)
	if player and player.has_method("respawn"):
		player.respawn(last_rest_point_position)
	player_respawned.emit()


func rest_at_point(point_id: String, point_position: Vector3) -> void:
	last_rest_point_id = point_id
	last_rest_point_position = point_position
	last_rest_point_scene = current_scene_path
	change_state(State.RESTING)
	if player and player.has_method("full_restore"):
		player.full_restore()
	SaveManager.save_game()
	_reset_enemies()
	change_state(State.PLAYING)


func _reset_enemies() -> void:
	var enemies := get_tree().get_nodes_in_group("enemies")
	for enemy in enemies:
		if enemy.has_method("reset_to_spawn"):
			enemy.reset_to_spawn()


func is_boss_defeated(boss_id: String) -> bool:
	return boss_id in defeated_bosses


func defeat_boss(boss_id: String) -> void:
	if boss_id not in defeated_bosses:
		defeated_bosses.append(boss_id)


func is_unique_defeated(unique_id: String) -> bool:
	return unique_id in defeated_uniques


func defeat_unique(unique_id: String) -> void:
	if unique_id not in defeated_uniques:
		defeated_uniques.append(unique_id)


func start_new_game() -> void:
	souls = 0
	dropped_souls_amount = 0
	dropped_souls_scene = ""
	defeated_bosses.clear()
	defeated_uniques.clear()
	last_rest_point_id = ""
	last_rest_point_scene = ""
	last_rest_point_position = Vector3.ZERO
	pending_player_data.clear()
	change_state(State.LOADING)
	current_scene_path = "res://scenes/world/test_arena.tscn"
	get_tree().change_scene_to_file(current_scene_path)


func continue_game() -> void:
	if SaveManager.load_game():
		change_state(State.LOADING)
		if current_scene_path.is_empty():
			current_scene_path = "res://scenes/world/test_arena.tscn"
		get_tree().change_scene_to_file(current_scene_path)


func get_save_data() -> Dictionary:
	return {
		"souls": souls,
		"dropped_souls_position": [dropped_souls_position.x, dropped_souls_position.y, dropped_souls_position.z],
		"dropped_souls_amount": dropped_souls_amount,
		"dropped_souls_scene": dropped_souls_scene,
		"current_scene": current_scene_path,
		"rest_point_id": last_rest_point_id,
		"rest_point_scene": last_rest_point_scene,
		"rest_point_position": [last_rest_point_position.x, last_rest_point_position.y, last_rest_point_position.z],
		"defeated_bosses": defeated_bosses,
		"defeated_uniques": defeated_uniques,
	}


func load_save_data(data: Dictionary) -> void:
	souls = data.get("souls", 0)
	var dsp: Array = data.get("dropped_souls_position", [0, 0, 0])
	dropped_souls_position = Vector3(dsp[0], dsp[1], dsp[2]) if dsp.size() >= 3 else Vector3.ZERO
	dropped_souls_amount = data.get("dropped_souls_amount", 0)
	dropped_souls_scene = data.get("dropped_souls_scene", "")
	current_scene_path = data.get("current_scene", "")
	last_rest_point_id = data.get("rest_point_id", "")
	last_rest_point_scene = data.get("rest_point_scene", "")
	var rpp: Array = data.get("rest_point_position", [0, 0, 0])
	last_rest_point_position = Vector3(rpp[0], rpp[1], rpp[2]) if rpp.size() >= 3 else Vector3.ZERO
	defeated_bosses = data.get("defeated_bosses", [])
	defeated_uniques = data.get("defeated_uniques", [])
	souls_changed.emit(souls)
