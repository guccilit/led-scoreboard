"""League definitions. Adding a league (e.g. NBA) means adding an entry here."""
from dataclasses import dataclass


@dataclass(frozen=True)
class League:
    key: str              # our id and ESPN league slug
    sport: str            # ESPN sport slug
    name: str
    period: str           # short period label: P1, Q3, 7th inning...
    division_spots: int   # playoff spots won straight from the division
    wildcards: int        # wild card spots per conference
    series: bool          # playoffs are best-of series
    points: bool          # standings ranked by points (NHL) instead of win %
    divisions: int        # sanity check for ESPN data
    team_count: tuple     # (min, max) sanity check for ESPN data
    season_games: int     # regular-season games per team
    ties_label: str = ""  # third record column: "OTL" (NHL), "T" (NFL)
    playin: int = 0       # NBA: seeds 7-10 go to the play-in (second cutoff line)


LEAGUES = {
    "nhl": League("nhl", "hockey", "NHL", "P", 3, 2, True, True, 4, (30, 36), 82, "OTL"),
    "mlb": League("mlb", "baseball", "MLB", "INN", 1, 3, True, False, 6, (28, 34), 162),
    "nfl": League("nfl", "football", "NFL", "Q", 1, 3, False, False, 8, (30, 36), 17, "T"),
    "nba": League("nba", "basketball", "NBA", "Q", 0, 6, True, False, 6, (28, 34), 82, playin=10),
}

# Rotation order in slides mode
ORDER = ["nhl", "mlb", "nfl", "nba"]
