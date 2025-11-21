import os

def batch_replace_in_files(root_dir, search_str, replace_str, extensions=(".py", ".md")):
    """
    Recursively finds and replaces text in all files with matching extensions.
    """
    print(f"\n--- Scanning for '{search_str}' -> '{replace_str}' ---")
    count = 0
    for root, dirs, files in os.walk(root_dir):
        # Skip the .git folder and the script itself
        if ".git" in root: continue
        
        for filename in files:
            if filename.endswith(extensions) and filename != "fix_paths.py":
                filepath = os.path.join(root, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    if search_str in content:
                        new_content = content.replace(search_str, replace_str)
                        with open(filepath, 'w', encoding='utf-8') as f:
                            f.write(new_content)
                        print(f"  [FIXED] {filename}")
                        count += 1
                except Exception as e:
                    print(f"  [ERROR] Could not read {filename}: {e}")
    
    print(f"--- Complete. Updated {count} files. ---")

if __name__ == "__main__":
    # 1. Fix the Assets Path (The Logo Issue)
    # Replaces any reference to "assets" with "assets"
    batch_replace_in_files(".", "assets", "assets")

    # 2. Fix the "Frontend" Naming (Your previous request)
    # Replaces "Frontend" with "GUI" in code references (imports, strings)
    # batch_replace_in_files(".", "Frontend", "GUI")  # <-- Uncomment if you still want this