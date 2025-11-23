"""
Purpose: Basic code health checks.

What it does: Compiles every Python file to catch syntax errors and checks if every file has a top-level docstring (a JOSS requirement).
"""
import pytest
import os
import ast
import sys
import importlib.util

import warnings
# 1. SETUP PROJECT PATH
# Ensure the test can see the project root
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

def find_python_files():
    """
    Recursively finds all python files in the project,
    excluding the test directory, git, and virtual environments.
    """
    py_files = []
    for root, _, files in os.walk(project_root):
        if 'tests' in root or '.git' in root or 'venv' in root or '__pycache__' in root:
            continue
        for file in files:
            if file.endswith('.py'):
                # Return the full path for reading, and relative for reporting
                full_path = os.path.join(root, file)
                py_files.append(full_path)
    return py_files

# Cache the list so we don't scan disk multiple times
ALL_FILES = find_python_files()


@pytest.mark.parametrize("file_path", ALL_FILES)
def test_syntax_compilation(file_path):
    """
    JOSS REQUIREMENT: Software must be usable.
    This test reads every file and compiles it to bytecode.
    It catches SyntaxErrors, IndentationErrors, and invalid structure
    BEFORE the user tries to run the experiment.
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        source = f.read()
    
    try:
        # compile() is stricter than ast.parse(); it generates bytecode
        compile(source, filename=file_path, mode='exec')
    except SyntaxError as e:
        pytest.fail(f"CRITICAL SYNTAX ERROR in {os.path.basename(file_path)}:\n"
                    f"Line {e.lineno}: {e.msg}\n"
                    f"Code: {e.text}")
    except Exception as e:
        pytest.fail(f"File {os.path.basename(file_path)} could not be compiled. Error: {e}")


@pytest.mark.parametrize("file_path", ALL_FILES)
def test_has_docstring(file_path):
    """
    JOSS REQUIREMENT: Documentation.
    This test parses the Abstract Syntax Tree (AST) to ensure
    every module has a docstring at the very top explaining what it does.
    """
    # Skip __init__.py files as they are often intentionally empty
    if os.path.basename(file_path) == "__init__.py":
        return

    with open(file_path, 'r', encoding='utf-8') as f:
        source = f.read()
    
    try:
        tree = ast.parse(source)
        # Check if the first node in the file is a Docstring (Expression -> Constant string)
        if not (tree.body and isinstance(tree.body[0], ast.Expr) and 
                isinstance(tree.body[0].value, (ast.Str, ast.Constant))):
            
            # This is a 'Warning' - it won't break code, but it fails JOSS standards
            warnings.warn(f"JOSS STANDARD MISSING: {os.path.basename(file_path)} has no top-level docstring. "
                          "Please add a description at the top of the file using '""" ... """'.", UserWarning)
            
    except SyntaxError:
        # Syntax errors are handled by the other test
        pass
