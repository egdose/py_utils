# py_utils

## Overview

This repository contains various Python utilities to help with different tasks. Below is a description of the utilities available in this repository.

## Utilities

### py_translate_dir.py

This script translates the names of files and directories within a specified directory to English using the Google Translate API.

#### Functions

- `translate_name(name, translator, fast_translation=False)`: Translates a given name to English if it contains non-English characters.
- `translate_directory(path, translator)`: Translates all file and directory names in the specified path.

#### Usage

Run the script with the directory path as an argument.

##### Arguments

- `<directory_path>`: Path of the directory to translate.

##### Example

```sh
python py_translate_dir.py /path/to/directory
```

### py_http_dir_traversal.py

This script implements an HTTP server that serves the contents of a specified directory and supports directory traversal. It also includes functionality to translate file and directory names using Google Translate.

#### Classes

- `DirectoryHandler(http.server.SimpleHTTPRequestHandler)`: Handles HTTP requests and provides directory listing with optional translation of file and directory names.

#### Functions

- `pretranslate_directory(path, translator)`: Pre-translates all file and directory names in the specified path and caches the translations.
- `signal_handler(signal, frame)`: Handles shutdown signals to gracefully stop the server.

#### Usage

Run the script with optional arguments to specify the port, directory, and translation options.

##### Arguments

- `--port, -p`: Port number to serve on (default: 8000)
- `--directory, -d`: Directory to serve (default: current directory)
- `--translate, -t`: Translate file and directory names (default: False)
- `--pretranslate, -pt`: Pre-translate all file and directory names (default: False)
- `--quicktranslate, -qt`: Enable quick translation mode (default: False)

##### Example

```sh
python py_http_dir_traversal.py --port 8080 --directory /path/to/serve --translate
```

### setup.bat

A batch script to set up a Python virtual environment and install the required dependencies.

#### Usage

```sh
setup.bat
```

## Setup

1. Clone the repository:
    ```sh
    git clone <repository_url>
    cd py_utils
    ```

2. Run the setup script to create a virtual environment and install dependencies:
    ```sh
    setup.bat
    ```

## Requirements

- Python 3.x
- `googletrans` library

## License

This project is licensed under the MIT License.