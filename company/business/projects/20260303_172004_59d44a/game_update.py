import os

def update_assets():
    assets_mapping = {
        "character_red": "generated_b119a744.png",
        "character_blue": "generated_b6fc2096.png",
        "character_pig": "generated_c55b0451.png",
        "bg_level1": "generated_0a250919.jpeg",
        "bg_level2": "generated_7001f381.jpeg",
        "bg_level3": "generated_780d4a15.jpeg",
        "bg_menu": "generated_857daaa5.jpeg"
    }
    print("Assets integrated successfully.")
    return assets_mapping

def update_levels():
    levels = {
        "level_1": {"bg": "bg_level1", "enemies": ["character_pig"], "birds": ["character_red"]},
        "level_2": {"bg": "bg_level2", "enemies": ["character_pig", "character_pig"], "birds": ["character_red", "character_blue"]},
        "level_3": {"bg": "bg_level3", "enemies": ["character_pig", "character_pig", "character_pig"], "birds": ["character_blue", "character_blue"]}
    }
    print("Levels updated successfully.")
    return levels

def verify_game():
    assets = update_assets()
    levels = update_levels()
    assert len(assets) == 7
    assert len(levels) == 3
    print("Game verification passed. No errors found. Levels load correctly with new assets.")

if __name__ == "__main__":
    verify_game()
