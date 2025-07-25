#!/usr/bin/env python3

# Standard libraries
import os # allows the interaction with the operating system, like handling files, folders...
import sys
import time
import subprocess
import threading
import requests
import json
# Parsing and networking
from argparse import ArgumentParser, RawTextHelpFormatter
from urllib.parse import urlparse
from argparse import RawTextHelpFormatter
# Output formatting
import csv
import pandas as pd
# Logging
import logging

# ─── Modules ──────────────────────────────────────────────────────────────────
current_dir = os.path.dirname(os.path.abspath(__file__)) # Get current directory
sys.path.append(os.path.join(current_dir, "modules")) # This must come before the import, or Python won't know where to find the visualization module
from whatweb_display import display_whatweb_result 
from nmap_display import display_nmap_result 
from ffuf_display import display_ffuf_result
from nikto_display import display_nikto_result
from wafwoof_display import display_wafwoof_result
from summary_display import display_tool_summary
from banner_display import display_banner
seclist_file = os.path.join(current_dir, "utils", "seclist_discovery.txt")

# ─── Params ──────────────────────────────────────────────────────────────────

parser = ArgumentParser(
    description="""
    WHACK (Web Hacking Automated Containerized Kit) is an automated web reconnaissance and vulnerability scanning tool. 
    It runs multiple tools against a given target and consolidates the results into a summary CSV and formatted output.

    Tools used:
    - Nmap: Service and port scanning
    - WhatWeb: Technology fingerprinting
    - WafW00f: WAF detection
    - Ffuf: Content and directory discovery
    - Nikto: Vulnerability scanning

    Note: If you're running WHACK inside a Docker container, make sure the target is accessible from within the container.
    To scan services running on your local machine (e.g. http://127.0.0.1), use '--network host' when starting the Docker container (Linux only).
    """,
    formatter_class=RawTextHelpFormatter # to preserve the line breaks of the description
)

parser.add_argument('-u', '--url', type=str, required=True, help="Target URL. Example: http://example.com")
parser.add_argument('-p', '--port', type=str, default="80,443", help="Target port(s), comma-separated. Default: 80,443")
parser.add_argument('-t', '--tool', type=str, default="all", help="Tools to run, comma-separated. Options: all (default), nmap, whatweb, wafwoof, ffuf, nikto")
args = parser.parse_args()

# ─── Globals ──────────────────────────────────────────────────────────────────
url = args.url
ports = args.port
tools=args.tool
target_ip = urlparse(url).hostname # To extract IP from the URL
results = []
spinner_running = False
allowed_tools = ["nmap", "whatweb", "wafwoof", "ffuf", "nikto"]

# ─── Tools and Commands ────────────────────────────────────────────────────────
commands = {
    # General Scanning
    "Nmap": ["nmap", "-sV", "-p", ports, target_ip],
    # Technology Fingerprint
    "Whatweb": ["whatweb", url],
    "Wafwoof": ["wafw00f", url],
    # Directory Enumeration (brute force). Content discovery
    "Ffuf": ["ffuf", "-u", f"{url}/FUZZ" , "-w", seclist_file, "-of", "json", "-o", "./outputs/ffuf_result.json"], 
    # Vulnerability Scanning
    "Nikto": ["nikto", "-h", url]
}

def filterCommands():
    if tools == "all":
        return commands
    selected_tools = [tool.strip().lower() for tool in tools.split(",")]
    allowed_lower = [t.lower() for t in allowed_tools]

    invalid_tools = [tool for tool in selected_tools if tool not in allowed_lower]
    if invalid_tools:
        logging.error(f"Invalid tool(s) specified: {', '.join(invalid_tools)}")
        sys.exit(1)

    # Map from lowercase tool name to original command key
    key_map = {key.lower(): key for key in commands.keys()}
    filtered = {key_map[tool]: commands[key_map[tool]] for tool in selected_tools}
    return filtered

# ─── Spinner ─────────────────────────────────────────────────────────────
class Spinner:
    def __init__(self, message="Processing"):
        self.spinner = ['|', '/', '-', '\\']
        self.stop_running = False
        self.thread = None
        self.message = message

    def start(self):
        self.stop_running = False
        self.thread = threading.Thread(target=self._spin)
        self.thread.start()

    def _spin(self):
        i = 0
        while not self.stop_running:
            sys.stdout.write(f"\r{self.message} {self.spinner[i % len(self.spinner)]}")
            sys.stdout.flush()
            time.sleep(0.1)
            i += 1

    def stop(self):
        self.stop_running = True
        if self.thread is not None:
            self.thread.join()
        sys.stdout.write("\r" + " " * (len(self.message) + 2) + "\r")
        sys.stdout.flush()

# ─── Log Setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("scan_log.log"),
        logging.StreamHandler() # To also show the log in the terminal 
    ]
)

display_banner()
logging.info(f"Target URL: {url}")
logging.info(f"Ports to scan: {ports}")

def log_checking_codes(tool_name, result, output):
    output = output.strip()
    # Handle non-zero exit codes
    if result.returncode != 0:
        if tool_name == "Nikto":
            if "Nikto v" in output and ("+ Target" in output or "+ Server:" in output):
                logging.info(f"{tool_name} executed successfully.")
            else:
                logging.error("Nikto failed: no valid output returned.")
                return f"Error: {output}"
        else:
            logging.error(f"{tool_name} failed with exit code {result.returncode}")
            return f"Error: {output}"
    else:
        logging.info(f"{tool_name} executed successfully.")
    return output

def get_ffuf_results():
    ffuf_json_path = "./outputs/ffuf_result.json"
    with open(ffuf_json_path, "r") as file:
        data = json.load(file)
    return data.get("results", [])

# Send a fake request to determine the length of a wildcard response.
def get_wildcard_length():
    fake_url = f"{url}/zz_fake_wildcard_check_path_999"
    try:
        resp = requests.get(fake_url, timeout=5)
        return len(resp.text)
    except Exception as e:
        logging.warning(f"[Ffuf] Wildcard detection failed: {e}")
        return None

def filter_by_length(
    ffuf_results, wildcard_length):
        return [result for result in ffuf_results if result["length"] != wildcard_length]

for tool_name, cmd in filterCommands().items():
    logging.info(f"Running {tool_name}")
    spinner = Spinner(f"Running {tool_name}")
    spinner.start()
    
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    
    spinner.stop()
    
    output = result.stdout  # TODO: refactor this, I don't like variable mutation
    output = log_checking_codes(tool_name, result, output)

    # Clean outputs
    if tool_name == "Wafwoof":
        for line in output.splitlines():
            if "No WAF detected" in line or "is behind" in line:
                output = line
                break
    elif tool_name == "Whatweb":
        output = output.split(" ", 1)[1]
    elif tool_name == "Nmap":
        filtered_lines = []
        for line in output.splitlines():
            if not (
                line.startswith("Starting Nmap") or
                line.startswith("Nmap scan report for") or
                line.startswith("Host is up") or
                line.startswith("Service detection performed.") or
                line.startswith("Nmap done:")
            ):
                filtered_lines.append(line)
        output = "\n".join(filtered_lines).strip()
    elif tool_name == "Ffuf":
        ffuf_results = get_ffuf_results()
        wildcard_length = get_wildcard_length()
        if wildcard_length is not None:
            filtered_results = filter_by_length(ffuf_results, wildcard_length)
            output = "\n".join(
                f'{r["input"]["FUZZ"]} [Status: {r["status"]}, Size: {r["length"]}, Words: {r["words"]}, Lines: {r["lines"]}, Words: {r["words"]}]' # TODO: I'd prefer shows all the response, not only these properties
                for r in filtered_results
            )
    elif tool_name == "Nikto":
        seen_paths = set()
        filtered_lines = []

        for line in output.splitlines():
            if not line.startswith("+") or line.startswith("+-"):
                continue  # Skip headers, formatting lines

            parts = line.split()
            path = next((p for p in parts if p.startswith("/")), None)

            if path:
                basename = os.path.splitext(os.path.basename(path))[0]
                if basename in seen_paths:
                    continue
                seen_paths.add(basename)

            filtered_lines.append(line.strip())

        output = "\n".join(filtered_lines)

    results.append({
        "Tool": tool_name,
        "Result": output.strip()
    })

# ─── Save Results to CSV ───────────────────────────────────────────────────────
csv_filename = "scan_results.csv"
logging.info(f"Saving results to {csv_filename}")

with open("scan_results.csv", "w", newline="") as csvfile:
    fieldnames = ["Tool", "Result"]
    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

    writer.writeheader()
    for row in results:
        writer.writerow(row)

# ─── Display ───────────────────────────────────────────────────────────
logging.info("Reading and displaying results...")

csv_file = pd.read_csv("scan_results.csv")

# -- nmap --
nmap_row = csv_file[csv_file["Tool"] == "Nmap"]
if not nmap_row.empty:
    raw_output = nmap_row["Result"].values[0]
    display_nmap_result(raw_output)

# -- WhatWeb --
whatweb_row = csv_file[csv_file["Tool"] == "Whatweb"]
if not whatweb_row.empty:
    raw_output = whatweb_row["Result"].values[0]
    display_whatweb_result(raw_output)

# -- Wafwoof --
wafwoof = csv_file[csv_file["Tool"] == "Wafwoof"]
if not wafwoof.empty:
    raw_output = wafwoof["Result"].values[0]
    display_wafwoof_result(raw_output)

# -- Ffuf --
ffuf_row = csv_file[csv_file["Tool"] == "Ffuf"]
if not ffuf_row.empty:
    raw_output = ffuf_row["Result"].values[0]
    display_ffuf_result(raw_output)
    
# -- Nikto --
nikto_row = csv_file[csv_file["Tool"] == "Nikto"]
if not nikto_row.empty:
    raw_output = nikto_row["Result"].values[0]
    display_nikto_result(raw_output)
    
display_tool_summary(results)
    
logging.info(f"Scan complete. Results saved to {csv_filename}")
