"""Lookup tables for IDs used in the F1 25 UDP specification."""

SUPPORTED_FORMATS = (2025, 2026)  # 2026 = F1 25 with the 2026 Season Pack
MAX_CARS = 24                     # 22 in format 2025, 24 in format 2026

# Packet IDs
MOTION, SESSION, LAP_DATA, EVENT, PARTICIPANTS, CAR_SETUPS, CAR_TELEMETRY, CAR_STATUS = range(8)
FINAL_CLASSIFICATION, LOBBY_INFO, CAR_DAMAGE, SESSION_HISTORY, TYRE_SETS, MOTION_EX = range(8, 14)
TIME_TRIAL, LAP_POSITIONS, CAR_TELEMETRY_2 = 14, 15, 16

# Wheel arrays in the UDP spec are always ordered RL, RR, FL, FR
WHEELS = ("RL", "RR", "FL", "FR")

TRACKS = {
    0: "Melbourne", 1: "Paul Ricard", 2: "Šanghaj", 3: "Sakhir", 4: "Barcelona", 5: "Monako",
    6: "Montreal", 7: "Silverstone", 8: "Hockenheim", 9: "Hungaroring", 10: "Spa-Francorchamps",
    11: "Monza", 12: "Singapur", 13: "Suzuka", 14: "Abú Dhabí", 15: "Austin", 16: "Interlagos",
    17: "Red Bull Ring", 18: "Soči", 19: "Mexiko", 20: "Baku", 21: "Sakhir (krátký)",
    22: "Silverstone (krátký)", 23: "Austin (krátký)", 24: "Suzuka (krátká)", 25: "Hanoj",
    26: "Zandvoort", 27: "Imola", 28: "Portimão", 29: "Džidda", 30: "Miami", 31: "Las Vegas",
    32: "Losail", 42: "Madrid", 39: "Silverstone (reverse)", 40: "Red Bull Ring (reverse)", 41: "Zandvoort (reverse)",
}

SESSION_TYPES = {
    0: "Neznámá", 1: "Trénink 1", 2: "Trénink 2", 3: "Trénink 3", 4: "Krátký trénink",
    5: "Kvalifikace Q1", 6: "Kvalifikace Q2", 7: "Kvalifikace Q3", 8: "Krátká kvalifikace",
    9: "Kvalifikace na jedno kolo", 10: "Sprint Shootout 1", 11: "Sprint Shootout 2",
    12: "Sprint Shootout 3", 13: "Krátký Sprint Shootout", 14: "Sprint Shootout na jedno kolo",
    15: "Závod", 16: "Závod 2", 17: "Závod 3", 18: "Time Trial",
}
RACE_SESSIONS = {15, 16, 17}
QUALI_SESSIONS = set(range(5, 15))
PRACTICE_SESSIONS = {1, 2, 3, 4}

WEATHER = {0: "Jasno", 1: "Polojasno", 2: "Zataženo", 3: "Slabý déšť", 4: "Silný déšť", 5: "Bouřka"}

# id -> (name, colour)
TEAMS = {
    0: ("Mercedes", "#27F4D2"), 1: ("Ferrari", "#E8002D"), 2: ("Red Bull Racing", "#3671C6"),
    3: ("Williams", "#64C4FF"), 4: ("Aston Martin", "#229971"), 5: ("Alpine", "#FF87BC"),
    6: ("Racing Bulls", "#6692FF"), 7: ("Haas", "#B6BABD"), 8: ("McLaren", "#FF8000"),
    9: ("Kick Sauber", "#52E252"), 41: ("F1 Generic", "#9aa4ad"), 104: ("Vlastní tým", "#c9a227"),
}
# 2026 Season Pack teams (476-486): the ten existing teams in their 2026 order, plus Audi and Cadillac
TEAMS.update({476 + i: TEAMS[i] for i in range(9)})
TEAMS.update({485: ("Audi", "#C40F2F"), 486: ("Cadillac", "#D4D7DB")})
DEFAULT_TEAM = ("—", "#9aa4ad")

# visual compound id -> (short, name, colour)
VISUAL_COMPOUNDS = {
    16: ("S", "měkké", "#ff3b3b"), 17: ("M", "střední", "#ffd23b"), 18: ("H", "tvrdé", "#f2f2f2"),
    7: ("I", "přechodné", "#3bd16f"), 8: ("W", "mokré", "#3b8bff"),
    19: ("SS", "super měkké", "#ff3b3b"), 20: ("S", "měkké", "#ffd23b"), 21: ("M", "střední", "#f2f2f2"),
    22: ("H", "tvrdé", "#f2f2f2"), 15: ("W", "mokré", "#3b8bff"),
}
ACTUAL_COMPOUNDS = {16: "C5", 17: "C4", 18: "C3", 19: "C2", 20: "C1", 21: "C0", 22: "C6", 7: "Inter", 8: "Wet"}
WET_COMPOUNDS = {7, 8, 15}

ERS_MODES = {0: "None", 1: "Medium", 2: "Hotlap", 3: "Overtake"}
FUEL_MIXES = {0: "Lean", 1: "Standard", 2: "Rich", 3: "Max"}
ERS_MAX_J = 4_000_000.0

RESULT_STATUS = {0: "—", 1: "neaktivní", 2: "na trati", 3: "v cíli", 4: "DNF", 5: "DSQ", 6: "NC", 7: "odstoupil"}
DRIVER_STATUS = {0: "v garáži", 1: "rychlé kolo", 2: "in-lap", 3: "out-lap", 4: "na trati"}

PENALTY_TYPES = {
    0: "průjezd boxy", 1: "stop and go", 2: "penalizace na startovním roštu", 3: "připomenutí penalizace",
    4: "časová penalizace", 5: "varování", 6: "diskvalifikace", 7: "vyřazení z formovacího kola",
    8: "příliš dlouhé stání", 9: "pravidla pneumatik", 10: "kolo zneplatněno",
    11: "toto i příští kolo zneplatněno", 12: "kolo zneplatněno", 13: "toto i příští kolo zneplatněno",
    14: "toto i předchozí kolo zneplatněno", 15: "toto i předchozí kolo zneplatněno",
    16: "odstoupení", 17: "černá vlajka",
}
LAP_INVALIDATED_PENALTIES = {10, 11, 12, 13, 14, 15}

# BUTN event bit flags: "UDP Action 1..12" are bindable in the game's control settings
UDP_ACTION_BITS = {n: 0x00100000 << (n - 1) for n in range(1, 13)}
