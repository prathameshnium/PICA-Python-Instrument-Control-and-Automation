import os
import re
import sys

# --- CONFIGURATION ---
# Set this to False to actually perform the changes
DRY_RUN = False

# Extensions to look for when updating references inside files
TEXT_EXTENSIONS = {'.py', '.md', '.txt', '.toml', '.json', '.yml', '.yaml'}

# Directories to ignore
IGNORE_DIRS = {'.git', '__pycache__', '.vscode', '.idea', 'venv', 'env', 'build', 'dist'}

def get_new_name(filename):
    """
    Removes _vXX, _VXX, vXX, or VXX from the end of a filename (before extension).
    Example: 'IV_K2400_GUI.py' -> 'IV_K2400_GUI.py'
    Example: 'PICA.py' -> 'PICA.py'
    """
    base, ext = os.path.splitext(filename)
    
    # Regex explanation:
    # _?       : Optional underscore
    # [vV]     : 'v' or 'V'
    # \d+      : One or more digits
    # $        : End of the string (filename base)
    pattern = r'_?[vV]\d+$'
    
    new_base = re.sub(pattern, '', base)
    
    if new_base != base:
        return new_base + ext
    return None

def main():
    root_dir = os.path.dirname(os.path.abspath(__file__))
    
    # 1. Identify all files that need renaming
    # mapping = { 'full/path/to/OldName.py': 'NewName.py' }
    renames = {}
    
    print("--- SCANNING FOR FILES TO RENAME ---")
    for dirpath, dirnames, filenames in os.walk(root_dir):
        # Filter out ignored directories
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        
        for filename in filenames:
            if filename == os.path.basename(__file__): continue # Don't rename this script
            
            new_name = get_new_name(filename)
            if new_name:
                full_old_path = os.path.join(dirpath, filename)
                renames[full_old_path] = new_name
                print(f"[RENAME DETECTED] {filename} -> {new_name}")

    if not renames:
        print("No files with version numbers found!")
        return

    # 2. Update references inside all text files
    # We must do this BEFORE renaming the actual files on disk
    print("\n--- UPDATING INTERNAL REFERENCES ---")
    
    count_updated_files = 0
    
    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        
        for filename in filenames:
            file_path = os.path.join(dirpath, filename)
            ext = os.path.splitext(filename)[1].lower()
            
            if ext not in TEXT_EXTENSIONS:
                continue

            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                
                original_content = content
                
                # Check for every file we are planning to rename
                for old_path, new_name in renames.items():
                    old_filename = os.path.basename(old_path)
                    
                    # 1. Replace "OldName.py" with "NewName.py" (Common in SCRIPT_PATHS dicts)
                    if old_filename in content:
                        content = content.replace(old_filename, new_name)
                    
                    # 2. Replace "OldName" with "NewName" (Common in imports)
                    # We accept this is risky but necessary for things like 'import OldName'
                    old_base = os.path.splitext(old_filename)[0]
                    new_base = os.path.splitext(new_name)[0]
                    
                    # Use regex to ensure we match whole words to avoid partial replacement issues
                    # e.g. replacing 'IV_Sweep' inside 'IV_Sweep_Two'
                    # But module imports are tricky. Let's stick to literal replacement for now 
                    # as your filenames are quite unique (e.g., PICA).
                    if old_base in content:
                         content = content.replace(old_base, new_base)

                if content != original_content:
                    print(f"[UPDATING CONTENT] {filename}")
                    if not DRY_RUN:
                        with open(file_path, 'w', encoding='utf-8') as f:
                            f.write(content)
                    count_updated_files += 1
            
            except Exception as e:
                print(f"Skipping {filename} due to error: {e}")

    # 3. Rename the files on disk
    print("\n--- PERFORMING FILE RENAMES ---")
    for old_path, new_name in renames.items():
        new_path = os.path.join(os.path.dirname(old_path), new_name)
        
        if DRY_RUN:
            print(f"(Dry Run) Move: {os.path.basename(old_path)} -> {new_name}")
        else:
            try:
                os.rename(old_path, new_path)
                print(f"Renamed: {os.path.basename(old_path)} -> {new_name}")
            except OSError as e:
                print(f"Error renaming {old_path}: {e}")

    print("\n" + "="*40)
    if DRY_RUN:
        print("COMPLETED DRY RUN. NO FILES CHANGED.")
        print("Check the output above. If it looks correct:")
        print("1. Open clean_filenames.py")
        print("2. Change DRY_RUN = True to DRY_RUN = False")
        print("3. Run the script again.")
    else:
        print("SUCCESS. Filenames cleaned and references updated.")
        print("Please run your tests now to ensure imports are still working.")
    print("="*40)

if __name__ == "__main__":
    main()