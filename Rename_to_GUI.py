import os

def main():
    print(">>> STARTING RENAME: GUI -> GUI <<<")
    
    # 1. Rename Files first (so we know the new paths)
    print("\n--- Renaming Files ---")
    for root, dirs, files in os.walk("."):
        # Skip git and deployment folders to be safe
        if ".git" in root or "deployment" in root:
            continue
            
        for filename in files:
            if "GUI" in filename and filename.endswith(".py"):
                old_path = os.path.join(root, filename)
                new_filename = filename.replace("GUI", "GUI")
                new_path = os.path.join(root, new_filename)
                
                try:
                    os.rename(old_path, new_path)
                    print(f"Renamed: {filename} -> {new_filename}")
                except OSError as e:
                    print(f"Error renaming {filename}: {e}")

    # 2. Update Code Content (Imports and String References)
    print("\n--- Updating Code References ---")
    for root, dirs, files in os.walk("."):
        if ".git" in root:
            continue
            
        for filename in files:
            if filename.endswith(".py") or filename.endswith(".md"):
                filepath = os.path.join(root, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    # Replace "GUI" with "GUI" in the code
                    # This updates 'import ..._GUI' and 'run_path("...GUI...")'
                    if "GUI" in content:
                        new_content = content.replace("GUI", "GUI")
                        
                        with open(filepath, 'w', encoding='utf-8') as f:
                            f.write(new_content)
                        print(f"Updated references in: {filename}")
                        
                except Exception as e:
                    print(f"Skipped reading/writing {filename}: {e}")

    print("\n>>> CLEANUP COMPLETE <<<")
    print("All 'GUI' files are now 'GUI'.")

if __name__ == "__main__":
    main()