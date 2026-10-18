extends Node

const SAVE_VERSION: int = 1
const SAVE_DIR: String = "user://saves/"
const SAVE_FILE: String = "save.json"
const BACKUP_FILE: String = "save.backup.json"
const TEMP_FILE: String = "save.tmp.json"

signal save_completed(success: bool)
signal load_completed(success: bool)


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	DirAccess.make_dir_recursive_absolute(SAVE_DIR)


func save_game() -> bool:
	var data := _collect_save_data()
	data["version"] = SAVE_VERSION
	data["timestamp"] = Time.get_datetime_string_from_system()
	var json_str := JSON.stringify(data, "\t")
	var temp_path := SAVE_DIR + TEMP_FILE
	var save_path := SAVE_DIR + SAVE_FILE
	var backup_path := SAVE_DIR + BACKUP_FILE
	var file := FileAccess.open(temp_path, FileAccess.WRITE)
	if not file:
		push_error("SaveManager: cannot open temp file: " + str(FileAccess.get_open_error()))
		save_completed.emit(false)
		return false
	file.store_string(json_str)
	file.close()
	var verify_file := FileAccess.open(temp_path, FileAccess.READ)
	if not verify_file:
		save_completed.emit(false)
		return false
	var verify_str := verify_file.get_as_text()
	verify_file.close()
	if verify_str.length() != json_str.length():
		push_error("SaveManager: write verification failed")
		save_completed.emit(false)
		return false
	if FileAccess.file_exists(save_path):
		DirAccess.rename_absolute(save_path, backup_path)
	DirAccess.rename_absolute(temp_path, save_path)
	save_completed.emit(true)
	return true


func load_game() -> bool:
	var save_path := SAVE_DIR + SAVE_FILE
	var backup_path := SAVE_DIR + BACKUP_FILE
	var data := _read_save_file(save_path)
	if data.is_empty():
		data = _read_save_file(backup_path)
		if data.is_empty():
			push_error("SaveManager: no valid save found")
			load_completed.emit(false)
			return false
		push_warning("SaveManager: primary save corrupt, restored from backup")
	var file_version: int = data.get("version", 0)
	if file_version > SAVE_VERSION:
		push_error("SaveManager: save version " + str(file_version) + " is newer than supported " + str(SAVE_VERSION))
		load_completed.emit(false)
		return false
	if file_version < SAVE_VERSION:
		data = _migrate_save(data, file_version)
	_apply_save_data(data)
	load_completed.emit(true)
	return true


func has_save() -> bool:
	return FileAccess.file_exists(SAVE_DIR + SAVE_FILE) or FileAccess.file_exists(SAVE_DIR + BACKUP_FILE)


func delete_save() -> void:
	var dir := DirAccess.open(SAVE_DIR)
	if dir:
		for f in [SAVE_FILE, BACKUP_FILE, TEMP_FILE]:
			if dir.file_exists(f):
				dir.remove(f)


func _read_save_file(path: String) -> Dictionary:
	if not FileAccess.file_exists(path):
		return {}
	var file := FileAccess.open(path, FileAccess.READ)
	if not file:
		return {}
	var text := file.get_as_text()
	file.close()
	if text.is_empty():
		return {}
	var json := JSON.new()
	var err := json.parse(text)
	if err != OK:
		push_error("SaveManager: JSON parse error in " + path + ": " + json.get_error_message())
		return {}
	if json.data is Dictionary:
		return json.data
	return {}


func _collect_save_data() -> Dictionary:
	var data: Dictionary = {}
	data["game"] = GameManager.get_save_data()
	var player := GameManager.player
	if player and player.has_method("get_save_data"):
		data["player"] = player.get_save_data()
	return data


func _apply_save_data(data: Dictionary) -> void:
	if data.has("game"):
		GameManager.load_save_data(data["game"])
	# Player data applied after scene loads — GameManager handles scene transition,
	# then player reads its data from GameManager.pending_player_data
	GameManager.pending_player_data = data.get("player", {})


func _migrate_save(data: Dictionary, from_version: int) -> Dictionary:
	var migrated := data.duplicate(true)
	# Future migrations go here: if from_version < 2: migrate 1→2, etc.
	migrated["version"] = SAVE_VERSION
	return migrated
