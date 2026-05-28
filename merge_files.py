import os

# Root directory containing .py files
ROOT_DIR = r"C:\Scraping\Mississippi-Procurement"

# Output merged file
OUTPUT_FILE = os.path.join(ROOT_DIR, "merged_python_files.txt")


def merge_py_files(root_dir, output_file):
    total_files = 0

    with open(output_file, "w", encoding="utf-8") as outfile:

        # Walk through all folders and subfolders
        for foldername, subfolders, filenames in os.walk(root_dir):

            for filename in filenames:
                if filename.endswith(".py"):

                    file_path = os.path.join(foldername, filename)

                    try:
                        # Write separator and file info
                        outfile.write("\n" + "=" * 120 + "\n")
                        outfile.write(f"FILE NAME : {filename}\n")
                        outfile.write(f"FILE PATH : {file_path}\n")
                        outfile.write("=" * 120 + "\n\n")

                        # Read and write file content
                        with open(file_path, "r", encoding="utf-8", errors="ignore") as infile:
                            content = infile.read()
                            outfile.write(content)

                        outfile.write("\n\n")
                        total_files += 1

                        print(f"Merged: {file_path}")

                    except Exception as e:
                        print(f"Error reading {file_path}: {e}")

    print("\n" + "=" * 60)
    print(f"Total .py files merged : {total_files}")
    print(f"Output file created    : {output_file}")
    print("=" * 60)


if __name__ == "__main__":
    merge_py_files(ROOT_DIR, OUTPUT_FILE)