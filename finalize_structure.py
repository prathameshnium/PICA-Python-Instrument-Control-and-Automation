import os
import shutil

def move_file(src, dst_folder):
    if os.path.exists(src):
        if not os.path.exists(dst_folder):
            os.makedirs(dst_folder)
        shutil.move(src, os.path.join(dst_folder, os.path.basename(src)))
        print(f"Moved: {src} -> {dst_folder}")

def update_file_content(file_path, old_str, new_str):
    """Replaces text in a file (e.g., changing _assets to assets)."""
    if not os.path.exists(file_path):
        return
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        if old_str in content:
            new_content = content.replace(old_str, new_str)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print(f"Updated paths in: {file_path}")
    except Exception as e:
        print(f"Could not update {file_path}: {e}")

def main():
    # --- 1. Create 'docs' folder and clean root ---
    print("--- Cleaning Root Directory ---")
    docs_dir = "docs"
    move_file("PICA_README.md", docs_dir)
    move_file("GPIB_Address_Guide.md", docs_dir)
    move_file("Change_Logs.md", docs_dir)
    move_file("project_paths_reference.txt", os.path.join(docs_dir, "dev_notes"))
    move_file("index.rst", docs_dir) # Assuming this is doc related

    # --- 2. Rename '_assets' to 'assets' ---
    print("\n--- Renaming Assets Folder ---")
    if os.path.exists("_assets"):
        if os.path.exists("assets"):
            print("Warning: 'assets' folder already exists. Merging...")
            # Simple merge logic could be added here, but for now we rename
            try:
                shutil.move("_assets", "assets_new")
                print("Renamed _assets to assets_new (please check manually)")
            except:
                pass
        else:
            os.rename("_assets", "assets")
            print("Renamed: _assets -> assets")
            
            # Update PICA_v6.py and README.md to point to new folder
            update_file_content("PICA_v6.py", "_assets", "assets")
            update_file_content("README.md", "_assets", "assets")
            # Also check the deployment script
            update_file_content("deployment/Picachu.py", "_assets", "assets")
            # And the new docs
            update_file_content(os.path.join(docs_dir, "PICA_README.md"), "_assets", "assets")

    # --- 3. Clean Deployment Folder ---
    # The video showed a 'Setup' folder inside deployment that seemingly duplicate files.
    # We will gently warn about this.
    if os.path.exists("deployment/Setup/Picachu.py") and os.path.exists("deployment/Picachu.py"):
        print("\n[!] NOTICE: You have 'Picachu.py' in both 'deployment/' and 'deployment/Setup/'.")
        print("    Reviewers might find this confusing. I recommend deleting the 'Setup' folder")
        print("    if it's just a backup, or renaming it if it serves a specific purpose.")

if __name__ == "__main__":
    main()