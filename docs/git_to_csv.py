import csv
import subprocess
import sys

# Configuration
OUTPUT_FILE = 'git_history.csv'

def run_command(command):
    """Runs a shell command and returns the output string."""
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            encoding='utf-8',
            errors='replace'
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Error running command: {' '.join(command)}\n{e}")
        return ""

def main():
    # 1. Get all commit hashes
    print("Fetching commit list...")
    # %H = Full Hash
    raw_hashes = run_command(['git', 'log', '--pretty=format:%H'])
    
    if not raw_hashes:
        print("No commits found.")
        return

    commit_hashes = raw_hashes.split('\n')
    total_commits = len(commit_hashes)
    
    print(f"Found {total_commits} commits. Processing...")

    # 2. Open CSV file for writing
    with open(OUTPUT_FILE, 'w', newline='', encoding='utf-8') as csvfile:
        fieldnames = ['Commit Hash', 'Date', 'Author', 'Message', 'Files Changed', 'Diff']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        # 3. Loop through every commit to extract details
        for i, commit_hash in enumerate(commit_hashes):
            # Progress indicator
            if i % 10 == 0:
                print(f"Processing {i}/{total_commits}...")

            # Get Date, Author, Message
            # %ad = author date, %an = author name, %B = raw body (message)
            metadata = run_command(['git', 'show', '-s', '--format=%ad|%an|%B', commit_hash])
            
            try:
                date, author, message = metadata.split('|', 2)
            except ValueError:
                # Handle cases where split fails due to unexpected characters
                date, author, message = ("", "", metadata)

            # Get Files Changed (name only)
            files_changed = run_command(['git', 'show', '--name-only', '--format=', commit_hash])
            
            # Get Full Diff
            full_diff = run_command(['git', 'show', '--format=', commit_hash])

            # Write to CSV
            writer.writerow({
                'Commit Hash': commit_hash,
                'Date': date,
                'Author': author,
                'Message': message.strip(),
                'Files Changed': files_changed,
                'Diff': full_diff
            })

    print(f"\nSuccess! Exported to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()