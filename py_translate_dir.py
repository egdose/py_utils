import os
import sys
from googletrans import Translator

import asyncio

async def translate_name(name, translator):
    translation = await translator.translate(name, dest='en')
    return translation.text

async def translate_directory(path, translator):
    total_files = sum([len(files) for r, d, files in os.walk(path)])
    translated_files = 0

    for root, dirs, files in os.walk(path, topdown=False):
        print(f"Path: {root}")

        for name in files:
            old_path = os.path.join(root, name)
            new_name = await translate_name(name, translator)
            new_path = os.path.join(root, new_name)
            os.rename(old_path, new_path)
            translated_files += 1
            print(f"File: {name} -> {new_name} ({translated_files}/{total_files})")

        for name in dirs:
            old_path = os.path.join(root, name)
            new_name = await translate_name(name, translator)
            new_path = os.path.join(root, new_name)
            os.rename(old_path, new_path)
            print(f"Directory: {name} -> {new_name}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python py_translate_dir.py <directory_path>")
        sys.exit(1)

    directory_path = sys.argv[1]
    translator = Translator()
    asyncio.run(translate_directory(directory_path, translator))