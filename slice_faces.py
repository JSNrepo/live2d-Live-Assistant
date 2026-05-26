from PIL import Image
import os

IMAGE_PATH = "Cute_face_expressions_set_hand_drawn_cartoon_art_illustration.jpg"
OUTPUT_DIR = "faces"
COLS = 4
ROWS = 5

EMOTIONS = [
    "pleading", "laughing", "owo", "excited",
    "shocked", "cheering", "curious", "gentle_smile",
    "exhausted", "flustered", "angry", "dizzy",
    "content", "winking", "playful", "relieved",
    "sobbing", "drooling", "nervous", "derp"
]

def slice_image():
    if not os.path.exists(IMAGE_PATH):
        print(f"(>_<) File {IMAGE_PATH} not found.")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    img = Image.open(IMAGE_PATH)
    img_width, img_height = img.size

    tile_width = img_width // COLS
    tile_height = img_height // ROWS

    print(f"(._.) Slicing {img_width}x{img_height} into {COLS}x{ROWS}...")

    idx = 0
    for r in range(ROWS):
        for c in range(COLS):
            left = c * tile_width
            top = r * tile_height
            right = left + tile_width
            bottom = top + tile_height
            face = img.crop((left, top, right, bottom))
            face.save(f"{OUTPUT_DIR}/{EMOTIONS[idx]}.jpg")
            idx += 1

    print("(⌐■_■) Done. Check 'faces/' directory.")

if __name__ == "__main__":
    slice_image()
