import os
import subprocess

def git_mv(old_path, new_path):
    if os.path.exists(old_path):
        # Create parent directory if it doesn't exist (for moving files)
        os.makedirs(os.path.dirname(new_path), exist_ok=True)
        print(f"Renaming: {old_path} -> {new_path}")
        subprocess.run(["git", "mv", old_path, new_path], check=True)

def main():
    # 1. Rename Directories
    # We walk bottom-up to ensure we don't rename a parent before its child is processed
    for root, dirs, files in os.walk(".", topdown=False):
        for name in dirs:
            if name == "Backends":
                old_dir = os.path.join(root, name)
                new_dir = os.path.join(root, "Instrument_Control")
                git_mv(old_dir, new_dir)

    # 2. Rename Files
    # We walk again to find files in the new directory structure
    for root, dirs, files in os.walk("."):
        for name in files:
            if "_Backend" in name or "_Backened" in name:
                # Replace variants (including the typo 'Backened')
                new_name = name.replace("_Backened", "_Instrument_Control")
                new_name = new_name.replace("_Backend", "_Instrument_Control")
                
                old_file = os.path.join(root, name)
                new_file = os.path.join(root, new_name)
                
                if old_file != new_file:
                    git_mv(old_file, new_file)

if __name__ == "__main__":
    main()