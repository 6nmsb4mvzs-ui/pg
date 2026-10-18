extends Node

@export var max_health: float = 100.0
@export var max_stamina: float = 100.0
@export var max_mana: float = 80.0
@export var stamina_regen_rate: float = 25.0
@export var stamina_regen_delay: float = 1.0
@export var mana_regen_rate: float = 3.0

var health: float = 100.0
var stamina: float = 100.0
var mana: float = 80.0

var level: int = 1
var vitality: int = 10
var endurance: int = 10
var strength: int = 10
var dexterity: int = 10
var intelligence: int = 10
var attunement: int = 10

var _stamina_regen_timer: float = 0.0
var _stamina_locked: bool = false

var defense: float = 0.0
var fire_resist: float = 0.0
var ice_resist: float = 0.0
var lightning_resist: float = 0.0
var shadow_resist: float = 0.0

signal health_changed(current: float, maximum: float)
signal stamina_changed(current: float, maximum: float)
signal mana_changed(current: float, maximum: float)
signal level_changed(new_level: int)
signal attribute_changed


func _ready() -> void:
	_recalculate_stats()
	health = max_health
	stamina = max_stamina
	mana = max_mana


func _process(delta: float) -> void:
	if _stamina_locked:
		_stamina_regen_timer -= delta
		if _stamina_regen_timer <= 0.0:
			_stamina_locked = false
	elif stamina < max_stamina:
		stamina = minf(stamina + stamina_regen_rate * delta, max_stamina)
		stamina_changed.emit(stamina, max_stamina)

	if mana < max_mana:
		mana = minf(mana + mana_regen_rate * delta, max_mana)
		mana_changed.emit(mana, max_mana)


func use_stamina(amount: float) -> bool:
	if stamina < amount * 0.3:
		return false
	stamina = maxf(stamina - amount, 0.0)
	_stamina_locked = true
	_stamina_regen_timer = stamina_regen_delay
	stamina_changed.emit(stamina, max_stamina)
	return true


func use_mana(amount: float) -> bool:
	if mana < amount:
		return false
	mana -= amount
	mana_changed.emit(mana, max_mana)
	return true


func apply_damage(amount: float) -> float:
	var reduced := maxf(amount - defense * 0.5, amount * 0.1)
	health = maxf(health - reduced, 0.0)
	health_changed.emit(health, max_health)
	return reduced


func heal(amount: float) -> void:
	health = minf(health + amount, max_health)
	health_changed.emit(health, max_health)


func full_restore() -> void:
	health = max_health
	stamina = max_stamina
	mana = max_mana
	_stamina_locked = false
	health_changed.emit(health, max_health)
	stamina_changed.emit(stamina, max_stamina)
	mana_changed.emit(mana, max_mana)


func get_damage_multiplier() -> float:
	return 1.0 + strength * 0.02 + dexterity * 0.01


func get_magic_multiplier() -> float:
	return 1.0 + intelligence * 0.03 + attunement * 0.015


func level_up(attribute: String) -> bool:
	var cost := _level_up_cost()
	if not GameManager.spend_souls(cost):
		return false
	match attribute:
		"vitality": vitality += 1
		"endurance": endurance += 1
		"strength": strength += 1
		"dexterity": dexterity += 1
		"intelligence": intelligence += 1
		"attunement": attunement += 1
		_: return false
	level += 1
	_recalculate_stats()
	level_changed.emit(level)
	attribute_changed.emit()
	return true


func _level_up_cost() -> int:
	return 100 + (level * level * 10)


func _recalculate_stats() -> void:
	max_health = 100.0 + vitality * 15.0
	max_stamina = 100.0 + endurance * 8.0
	max_mana = 80.0 + intelligence * 10.0 + attunement * 5.0
	stamina_regen_rate = 25.0 + endurance * 1.5
	mana_regen_rate = 3.0 + attunement * 0.5
	health = minf(health, max_health)
	stamina = minf(stamina, max_stamina)
	mana = minf(mana, max_mana)


func get_save_data() -> Dictionary:
	return {
		"health": health,
		"stamina": stamina,
		"mana": mana,
		"level": level,
		"vitality": vitality,
		"endurance": endurance,
		"strength": strength,
		"dexterity": dexterity,
		"intelligence": intelligence,
		"attunement": attunement,
	}


func load_save_data(data: Dictionary) -> void:
	level = data.get("level", 1)
	vitality = data.get("vitality", 10)
	endurance = data.get("endurance", 10)
	strength = data.get("strength", 10)
	dexterity = data.get("dexterity", 10)
	intelligence = data.get("intelligence", 10)
	attunement = data.get("attunement", 10)
	_recalculate_stats()
	health = data.get("health", max_health)
	stamina = data.get("stamina", max_stamina)
	mana = data.get("mana", max_mana)
	health_changed.emit(health, max_health)
	stamina_changed.emit(stamina, max_stamina)
	mana_changed.emit(mana, max_mana)
