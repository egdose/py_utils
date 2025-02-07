import os
from googletrans import Translator

def translate_name(name, translator):
    translation = translator.translate(name, dest='en')
    return translation.text

def translate_directory(path, translator):
    for root, dirs, files in os.walk(path, topdown=False):
        for name in files:
            old_path = os.path.join(root, name)
            new_name = translate_name(name, translator)
            new_path = os.path.join(root, new_name)
            os.rename(old_path, new_path)

        for name in dirs:
            old_path = os.path.join(root, name)
            new_name = translate_name(name, translator)
            new_path = os.path.join(root, new_name)
            os.rename(old_path, new_path)

if __name__ == "__main__":
    directory_path = input("Enter the directory path: ")
    translator = Translator()
    translate_directory(directory_path, translator)