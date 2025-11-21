import os
import datetime

# Configuration
OUTPUT_FILE = os.path.join("docs", "project_paths_reference.txt")

# Folders and files to exclude from the reference list
IGNORE_DIRS = {
    ".git", ".github", "__pycache__", ".venv", "venv", "env",
    ".vscode", ".idea", "build", "dist", "pica.egg-info"
}
IGNORE_FILES = {
    ".DS_Store", "Thumbs.db", "update_paths.py",
    "fix_project.py", "finalize_structure.py"
}


def generate_structure(startpath):
    output_lines = []

    for root, dirs, files in os.walk(startpath):
        # filter directories in-place to prevent walking into them
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]

        # Calculate indentation level
        level = root.replace(startpath, '').count(os.sep)
        indent = '    ' * level

        # Add the directory
        dirname = os.path.basename(root)
        if dirname == '.':
            dirname = "PICA (Root Directory)"
        output_lines.append(f"{indent}{dirname}/")

        # Add the files
        sub_indent = '    ' * (level + 1)
        for f in sorted(files):
            if f not in IGNORE_FILES:
                output_lines.append(f"{sub_indent}{f}")

    return "\n".join(output_lines)


def main():
    # Ensure docs directory exists
    if not os.path.exists("docs"):
        os.makedirs("docs")
        print("Created 'docs' directory.")

    print("Scanning directory structure...")
    structure_text = generate_structure(".")

    header = f"""PICA Project File Structure Reference
Generated on: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
================================================================================
"""

    try:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write(header + "\n")
            f.write(structure_text)
        print(f"✅ Successfully updated: {OUTPUT_FILE}")
    except Exception as e:
        print(f"❌ Error writing file: {e}")


if __name__ == "__main__":
    main()
