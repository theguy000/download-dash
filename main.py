#!/usr/bin/env python3

# --- Core Logic Imports ---
import subprocess
import os
import re
import glob
import shlex
from urllib.parse import urlparse, unquote
from datetime import datetime
import time
import shutil
import sys
import traceback # For detailed error reporting

# --- GUI Imports ---
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext
import ttkbootstrap as tb
from ttkbootstrap.constants import *
from http.server import BaseHTTPRequestHandler, HTTPServer
import json # For parsing data from helper script
import threading
import queue

# --- Library Import ---
try:
    import yt_dlp
except ImportError:
    print("ERROR: yt-dlp library not found. Please install it using: pip install -U yt-dlp", file=sys.stderr)
    # Attempt to show GUI error if tkinter is available
    try:
        root_err = tk.Tk()
        root_err.withdraw() # Hide main window
        tk.messagebox.showerror("Dependency Error", "Required library 'yt-dlp' not found.\nPlease install it:\n\npip install -U yt-dlp")
    except Exception:
        pass # Ignore if tkinter isn't even working
    sys.exit(1)

try:
    from colorama import init as colorama_init, Fore, Style
    colorama_init(autoreset=True)
    # Variable to control color output in core functions when run via GUI
    # Set to False in the __main__ block for GUI mode
    USE_COLOR = True
except ImportError:
    USE_COLOR = False
    # Define dummy classes/variables if colorama is not installed
    class Fore: pass
    class Style: pass
    Fore.RED=Fore.GREEN=Fore.YELLOW=Fore.BLUE=Fore.WHITE=''
    Style.DIM=Style.RESET_ALL=Style.BRIGHT=''
    # Assign empty strings directly
    Fore.RED = Fore.GREEN = Fore.YELLOW = Fore.BLUE = Fore.WHITE = ''
    Style.DIM = Style.RESET_ALL = Style.BRIGHT = ''

# --- Configuration ---
# IMPORTANT: Update these if mp4decrypt/ffmpeg are not in your system PATH
MP4DECRYPT_PATH = "mp4decrypt"
FFMPEG_PATH = "ffmpeg"
HELPER_SCRIPT_PORT = 12345 # Port for listening to the helper script
DEBUG_OUTPUT = False # Set to True for verbose command output in the GUI log

# ================================================
# ========= CORE PROCESSING LOGIC ================
# ================================================
# (Functions from the original script, slightly adapted for GUI context)

# --- Helper Print Functions (Now write to stdout/stderr for redirection) ---
def print_step(message):
    """Prints a step marker (goes to queue via redirection)."""
    prefix = f"{Style.BRIGHT}{Fore.BLUE}" if USE_COLOR else ""
    suffix = Style.RESET_ALL if USE_COLOR else ""
    print(f"\n{prefix}>>>>> {message} <<<<<{suffix}")

def print_success(message):
    """Prints a success message (goes to queue via redirection)."""
    prefix = f"{Fore.GREEN}" if USE_COLOR else "✔ "
    suffix = Style.RESET_ALL if USE_COLOR else ""
    print(f"{prefix}✔ {message}{suffix}")

def print_warning(message):
    """Prints a warning message (goes to queue via redirection)."""
    prefix = f"{Fore.YELLOW}" if USE_COLOR else "⚠ "
    suffix = Style.RESET_ALL if USE_COLOR else ""
    print(f"{prefix}⚠ {message}{suffix}")

def print_error(message):
    """Prints an error message to stderr (goes to queue via redirection)."""
    prefix = f"{Fore.RED}" if USE_COLOR else "✖ ERROR: "
    suffix = Style.RESET_ALL if USE_COLOR else ""
    print(f"{prefix}✖ ERROR: {message}{suffix}", file=sys.stderr)

def print_info(message):
    """Prints an informational message (goes to queue via redirection)."""
    prefix = f"{Fore.WHITE}" if USE_COLOR else ""
    suffix = Style.RESET_ALL if USE_COLOR else ""
    print(f"{prefix}{message}{suffix}")

def print_command(cmd_list):
    """Prints the command being run (goes to queue via redirection)."""
    prefix = f"{Style.DIM}" if USE_COLOR else ""
    suffix = Style.RESET_ALL if USE_COLOR else ""
    # Ensure all args are strings for shlex.quote and join
    safe_cmd_list = [str(arg) for arg in cmd_list]
    print(f"{prefix}Running command: {' '.join(shlex.quote(arg) for arg in safe_cmd_list)}{suffix}")

def check_tool(tool_path, tool_name):
    """Checks for external tools (prints results via redirection)."""
    print_info(f"Checking for '{tool_name}'...")
    tool_location = shutil.which(tool_path)
    if tool_location:
        print_success(f"Found '{tool_name}' at '{tool_location}'.")
        return True
    else:
        print_error(f"'{tool_name}' command not found at '{tool_path}'.")
        print_error(f"Please install '{tool_name}' and ensure its location is in your system's PATH.")
        print_error(f"Alternatively, edit the script and set {tool_name.upper()}_PATH correctly.")
        return False

# --- Filename Generation ---
def generate_filename(url):
    """Generates a filename based on the URL structure."""
    # (Code is identical to the original script)
    try:
        parsed_url = urlparse(url)
        path_parts = [unquote(part) for part in parsed_url.path.split('/') if part]
        date_match = re.search(r'/(\d{8})/[^/]+/([^/]+?)_(?:Episode_)?(\d{8})', parsed_url.path)
        if date_match:
            date_str1 = date_match.group(1); name_part = date_match.group(2); date_str2 = date_match.group(3)
            use_date_str = date_str1 if date_str1 == date_str2 else date_str1
            try:
                dt_obj = datetime.strptime(use_date_str, '%Y%m%d') # Changed format here if needed based on testing -> original was %d%m%Y - adjust if necessary
                # If the above format fails, try the original:
            except ValueError:
                 try:
                    dt_obj = datetime.strptime(use_date_str, '%d%m%Y'); formatted_date = dt_obj.strftime('%B %d %Y')
                    cleaned_name = re.sub(r'(_Episode|_hi|_low|_med|_sd|_hd|_vod.*)', '', name_part, flags=re.IGNORECASE).strip('_').replace('_', ' ')
                    return f"{cleaned_name} {formatted_date}"
                 except ValueError: print_warning(f"Could not parse date {use_date_str} in URL pattern 1 with known formats.")
            else:
                formatted_date = dt_obj.strftime('%B %d %Y')
                cleaned_name = re.sub(r'(_Episode|_hi|_low|_med|_sd|_hd|_vod.*)', '', name_part, flags=re.IGNORECASE).strip('_').replace('_', ' ')
                return f"{cleaned_name} {formatted_date}"

        for i in range(len(path_parts) - 1, -1, -1):
             segment = path_parts[i]
             if 'manifest.mpd' in segment.lower() or len(segment) > 40 or re.fullmatch(r'[a-f0-9-]{30,}', segment) or \
                segment.lower() in ['episode', 'dash', 'drm1', 'titanfile', 'tv_shows', 'media']: continue # Added 'media' here too
             # Try finding a likely filename part based on common patterns
             name = os.path.splitext(segment)[0]
             name = re.sub(r'(_hi|_low|_med|_sd|_hd|_vod.*|_\d{8}[_]?.*)', '', name, flags=re.IGNORECASE).strip('_').replace('_', ' ').replace('-', ' ').strip() # Improved cleanup
             if len(name) > 3 and not name.isdigit(): # Check for meaningful length and not just digits
                return name

        # Fallback using less specific parts
        meaningful_parts = [p for p in path_parts if p.lower() not in ['drm1', 'titanfile', 'dash', 'media', 'tv_shows', 'episode'] and len(p) > 3 and not p.isdigit()]
        if meaningful_parts:
            name = os.path.splitext(meaningful_parts[-1])[0] # Use last meaningful part
            name = re.sub(r'(_hi|_low|_med|_sd|_hd|_vod.*|_\d{8}[_]?.*)', '', name, flags=re.IGNORECASE).strip('_').replace('_', ' ').replace('-', ' ').strip()
            if len(name) > 3:
                 return name

        print_warning("Could not determine specific name from URL, using fallback.")
        return "downloaded_video_" + time.strftime("%Y%m%d_%H%M%S")
    except Exception as e:
        print_error(f"Error generating filename: {e}")
        # Print traceback to stderr for debugging (will go to queue)
        traceback.print_exc(file=sys.stderr)
        return "download_error_" + time.strftime("%Y%m%d_%H%M%S")

def sanitize_filename(name):
    """Cleans a string to be suitable for use as a filename."""
    name = re.sub(r'[\\/*?:"<>|\']', "", name) # Added '
    name = re.sub(r'\s+', "_", name).strip('_') # Replace whitespace with underscore
    name = re.sub(r'_+', '_', name) # Consolidate multiple underscores
    name = name[:180] # Limit length
    return name

# --- Core Processing Functions ---
def run_simple_command(command_list, step_name):
    """Runs simple commands (decrypt, merge) using subprocess."""
    print_step(f"Starting: {step_name}")
    print_command(command_list)
    try:
        # Hide console window on Windows
        creationflags = 0
        if os.name == 'nt':
            creationflags = subprocess.CREATE_NO_WINDOW

        result = subprocess.run(
            command_list, capture_output=True, text=True, encoding='utf-8', errors='replace',
            check=False, # Don't raise exception on failure, check returncode instead
            creationflags=creationflags
            )

        # Log stdout/stderr based on DEBUG_OUTPUT or if errors occurred
        if result.returncode == 0:
            print_success(f"{step_name} completed successfully.")
            if DEBUG_OUTPUT and result.stdout:
                print_info(f"--- {step_name} STDOUT ---\n{result.stdout.strip()}")
            if DEBUG_OUTPUT and result.stderr: # Still log stderr even on success if debug is on
                print_warning(f"--- {step_name} STDERR ---\n{result.stderr.strip()}")
            return True
        else:
            print_error(f"{step_name} failed with return code {result.returncode}.")
            # Always print stdout/stderr on failure
            if result.stdout:
                print_info(f"--- {step_name} STDOUT ---\n{result.stdout.strip()}")
            if result.stderr:
                print_error(f"--- {step_name} STDERR ---\n{result.stderr.strip()}")
            return False
    except FileNotFoundError:
        print_error(f"Command not found: {command_list[0]}. Ensure it's installed and in PATH.")
        return False
    except Exception as e:
        print_error(f"Unexpected error during {step_name}: {e}")
        traceback.print_exc(file=sys.stderr) # Print full traceback to stderr
        return False

# Original console progress hook (NOT used by GUI directly)
def download_progress_hook(d):
    """Progress hook for yt-dlp library (console version)."""
    if d['status'] == 'downloading':
        percent_str = d.get('_percent_str', '---%')
        speed_str = d.get('_speed_str', '--- B/s')
        eta_str = d.get('_eta_str', '--:--')
        fragment_index = d.get('fragment_index')
        fragment_count = d.get('fragment_count')
        terminal_width = shutil.get_terminal_size((80, 20)).columns

        if fragment_index and fragment_count:
             progress_line = f"[download] Fragment {fragment_index}/{fragment_count} "
             progress_line += f"({percent_str.strip():>6}) at {speed_str.strip():>10} ETA {eta_str.strip()}"
        else:
              progress_line = f"[download] {percent_str.strip():>6} "
              progress_line += f"at {speed_str.strip():>10} ETA {eta_str.strip()}"

        # Use standard print, will be captured by redirector if in thread
        print(f"\r{progress_line:<{terminal_width}}", end='')
        sys.stdout.flush() # Important!

    elif d['status'] == 'finished':
        print() # Newline after progress
        total_bytes = d.get('total_bytes')
        elapsed = d.get('elapsed')
        speed = d.get('speed')
        elapsed_str = f"{elapsed:.2f}s" if elapsed else "N/A"
        speed_str = f"{d.get('_speed_str', 'N/A')}" if speed else "N/A"
        size_str = f"{d.get('_total_bytes_str', 'N/A')}"
        print_success(f"Downloaded '{os.path.basename(d.get('filename', 'file'))}' ({size_str}) in {elapsed_str} (Avg speed: {speed_str})")

    elif d['status'] == 'error':
        print() # Newline before error
        print_error(f"Download error occurred for '{d.get('filename', 'file')}'")
        # yt-dlp might raise DownloadError anyway


# Modified download function accepting a custom hook
def download_video_library(url, output_template_abs, progress_hook_func=None):
    """Downloads using the yt-dlp library, optionally using a custom progress hook."""
    print_step("Starting: Download (yt-dlp library)")
    print_info(f"Output pattern: {output_template_abs}")

    ydl_opts = {
        'outtmpl': output_template_abs,
        'concurrent_fragment_downloads': 10, # Reduced slightly for stability
        'retries': 5, # Add retries
        'fragment_retries': 5, # Add fragment retries
        'allow_unplayable_formats': True,
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best', # More robust format selection
        'noprogress': True, # We use hooks
        'quiet': True, # Suppress yt-dlp's direct console output
        'no_warnings': True, # Suppress specific yt-dlp warnings
        'progress_hooks': [progress_hook_func] if progress_hook_func else [], # Use passed hook
        'noplaylist': True,
        'socket_timeout': 30, # Add socket timeout
        # 'cookiefile': 'path/to/cookies.txt',
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url]) # Pass URL as a list
        # Success is implicitly handled by the 'finished' status in the hook
        # or lack of exceptions.
        return True
    except yt_dlp.utils.DownloadError as e:
        # Error printed by the progress hook's 'error' status or here
        print_error(f"yt-dlp download failed: {e}")
        return False
    except Exception as e:
        print_error(f"An unexpected error occurred during yt-dlp download: {e}")
        traceback.print_exc(file=sys.stderr) # Print traceback via redirector
        return False

def find_downloaded_files(base_filepath_sans_ext):
    """Finds the actual downloaded video/audio files."""
    print_info(f"Searching for downloaded files matching: {base_filepath_sans_ext}.*")
    # Common extensions yt-dlp might use for mp4/m4a containers
    vid_patterns = [
        f"{base_filepath_sans_ext}.f*.mp4", # Often uses format codes like .f137.mp4
        f"{base_filepath_sans_ext}.mp4",
        f"{base_filepath_sans_ext}.*.mp4", # Generic pattern
    ]
    aud_patterns = [
        f"{base_filepath_sans_ext}.f*.m4a", # Often uses format codes like .f140.m4a
        f"{base_filepath_sans_ext}.m4a",
        f"{base_filepath_sans_ext}.*.m4a", # Generic pattern
        f"{base_filepath_sans_ext}.f*.aac", # Less common but possible
        f"{base_filepath_sans_ext}.aac",
    ]
    found_video, found_audio = None, None
    downloaded_files = glob.glob(f"{base_filepath_sans_ext}.*") # Get all related files first
    # Filter out intermediate files or final outputs from previous runs
    candidate_files = [f for f in downloaded_files if '_dec' not in f and '_final' not in f]

    print_info(f"Candidate files: {candidate_files}") # Debugging output

    # --- Find Video ---
    for p in vid_patterns:
        matches = [f for f in glob.glob(p) if '_dec' not in f and '_final' not in f]
        # Select the most likely video file (e.g., often largest for MP4, or check metadata if possible)
        if matches:
            # Simple approach: assume first match is good enough for now
            # More complex: check file size, or even use ffprobe if needed
            found_video = matches[0]
            print_info(f"Video candidate via pattern '{p}': {found_video}")
            break # Stop searching video patterns

    # --- Find Audio ---
    for p in aud_patterns:
        matches = [f for f in glob.glob(p) if '_dec' not in f and '_final' not in f]
        potential_audio_files = [f for f in matches if not (found_video and os.path.normpath(f) == os.path.normpath(found_video))]
        if potential_audio_files:
            # If there are files matching the audio pattern that are NOT the video file
            found_audio = potential_audio_files[0] # Take the first one
            print_info(f"Audio candidate via pattern '{p}': {found_audio}")
            break # Stop searching audio patterns


    # --- Check if video and audio were found in the same file (combined stream) ---
    if found_video and not found_audio:
         # Check if the found video file *also* matches an audio pattern
         # This implies yt-dlp downloaded a combined stream
         video_is_also_audio = any(glob.fnmatch.fnmatch(found_video, p) for p in aud_patterns)
         if video_is_also_audio:
             print_warning(f"Video file '{os.path.basename(found_video)}' seems to contain both video and audio.")
             found_audio = found_video # Use the same file for decryption step (might fail if not needed)


    # --- Process Results ---
    video_path, audio_path = None, None
    if found_video:
        video_path = os.path.abspath(found_video)
        print_success(f"Found Video: {video_path}")
    else:
        print_error("Downloaded video file not found matching patterns.")
        print_warning(f"Looked for patterns like: {vid_patterns}")
        print_warning(f"Files found in output dir: {downloaded_files}")
        return None, None # Critical failure if video not found

    if found_audio:
        audio_path = os.path.abspath(found_audio)
        # Don't print success if it's the same as the video file unless verbose
        if video_path != audio_path:
             print_success(f"Found Audio: {audio_path}")
        elif DEBUG_OUTPUT:
            print_info(f"Audio stream appears to be in the same file as video: {audio_path}")
    else:
        # If separate audio expected but not found
        print_error("Downloaded audio file not found matching patterns.")
        print_warning(f"Looked for patterns like: {aud_patterns}")
        print_warning(f"Files found in output dir: {downloaded_files}")
        return None, None # Critical failure if audio not found (and wasn't combined)

    return video_path, audio_path


def decrypt_files(key, video_in, audio_in):
    """Decrypts video and audio files using mp4decrypt."""
    if not video_in or not audio_in:
        print_error("Input files missing for decryption.")
        return None, None

    # Check if inputs are valid files
    if not os.path.isfile(video_in):
        print_error(f"Video input for decryption does not exist: {video_in}")
        return None, None
    if not os.path.isfile(audio_in):
        print_error(f"Audio input for decryption does not exist: {audio_in}")
        return None, None

    # Define output paths
    vid_base_abs, vid_ext = os.path.splitext(video_in)
    aud_base_abs, aud_ext = os.path.splitext(audio_in)
    decrypted_video_file = f"{vid_base_abs}_dec{vid_ext}"
    decrypted_audio_file = f"{aud_base_abs}_dec{aud_ext}"

    # Decrypt Video
    print_info(f"Attempting to decrypt video to: {os.path.basename(decrypted_video_file)}")
    cmd_vid = [MP4DECRYPT_PATH, '--key', key, video_in, decrypted_video_file]
    if not run_simple_command(cmd_vid, "Decrypt Video"):
        print_error("Video decryption command failed.")
        # Clean up partially decrypted file if it exists
        if os.path.exists(decrypted_video_file):
            try: os.remove(decrypted_video_file)
            except OSError: pass
        return None, None # Stop if video decryption fails

    # Decrypt Audio (only if it's a different file from video)
    if os.path.normpath(video_in) != os.path.normpath(audio_in):
        print_info(f"Attempting to decrypt audio to: {os.path.basename(decrypted_audio_file)}")
        cmd_aud = [MP4DECRYPT_PATH, '--key', key, audio_in, decrypted_audio_file]
        if not run_simple_command(cmd_aud, "Decrypt Audio"):
            print_error("Audio decryption command failed.")
            # Clean up successfully decrypted video file and partially decrypted audio file
            if os.path.exists(decrypted_video_file):
                try:
                    os.remove(decrypted_video_file)
                except OSError:
                    pass
            if os.path.exists(decrypted_audio_file):
                try:
                    os.remove(decrypted_audio_file)
                except OSError:
                    pass
            return None, None
    else:
        # Audio is same as video, decryption already done. Use the decrypted video path.
        print_info("Audio is in the same file as video, using already decrypted file.")
        decrypted_audio_file = decrypted_video_file # Use the same decrypted file for merging


    # Verify output files exist
    if not os.path.exists(decrypted_video_file) or os.path.getsize(decrypted_video_file) == 0:
        print_error(f"Decrypted video file missing or empty after command success: {decrypted_video_file}")
        # Clean up potentially empty/failed audio file if different
        if os.path.normpath(video_in) != os.path.normpath(audio_in) and os.path.exists(decrypted_audio_file):
             try:
                 os.remove(decrypted_audio_file)
             except OSError:
                 pass
        return None, None
    if os.path.normpath(video_in) != os.path.normpath(audio_in) and (not os.path.exists(decrypted_audio_file) or os.path.getsize(decrypted_audio_file) == 0):
        print_error(f"Decrypted audio file missing or empty after command success: {decrypted_audio_file}")
        # Clean up successful video file since audio failed
        if os.path.exists(decrypted_video_file):
            try:
                os.remove(decrypted_video_file)
            except OSError:
                pass
        return None, None

    return decrypted_video_file, decrypted_audio_file


def merge_files(video_in, audio_in, output_file):
    """Merges decrypted video and audio using ffmpeg."""
    if not video_in or not audio_in:
        print_error("Input files missing for merging.")
        return False
    if not os.path.isfile(video_in):
        print_error(f"Video input for merging does not exist: {video_in}")
        return False
    # Audio might be same file as video if combined stream was downloaded+decrypted
    if not os.path.isfile(audio_in):
         print_error(f"Audio input for merging does not exist: {audio_in}")
         return False

    print_info(f"Attempting to merge files into: {os.path.basename(output_file)}")
    # Base command: copy video and audio streams
    command = [
        FFMPEG_PATH,
        '-i', video_in,
        '-i', audio_in,
        '-c:v', 'copy',
        '-c:a', 'copy',
        '-loglevel', 'warning', # Show only warnings and errors
        '-stats', # Show progress during merge
        '-y', # Overwrite output without asking
        output_file
    ]

    # Adjust command if video and audio inputs are the same file
    if os.path.normpath(video_in) == os.path.normpath(audio_in):
        print_warning("Video and Audio inputs for merge are the same file. Assuming it's already muxed.")
        # Simply copy the single input file to the output filename
        try:
            print_step("Starting: Copying Muxed File (ffmpeg not needed)")
            print_command(["shutil.copy2", video_in, output_file]) # Show conceptually
            shutil.copy2(video_in, output_file)
            print_success("Copying Muxed File completed successfully.")
            return True
        except Exception as e:
            print_error(f"Failed to copy already muxed file: {e}")
            traceback.print_exc(file=sys.stderr)
            return False
        # Old approach using ffmpeg copy even for same file:
        # print_warning("Video and Audio inputs for merge are the same. Adjusting ffmpeg command.")
        # command = [ FFMPEG_PATH, '-i', video_in, '-map', '0:v?', '-map', '0:a?', '-c', 'copy', '-loglevel', 'warning', '-stats', '-y', output_file ]


    # Run the merge command
    if not run_simple_command(command, "Merge (ffmpeg)"):
        print_error("Merging command failed.")
        if os.path.exists(output_file): # Clean up failed merge attempt
            try:
                os.remove(output_file)
            except OSError:
                pass
        return False

    # Verify final output
    if not os.path.exists(output_file) or os.path.getsize(output_file) < 100: # Check size > 100 bytes sanity check
        print_error(f"Final merged file missing or empty after command success: {output_file}")
        return False

    return True

# ================================================
# ========= GUI IMPLEMENTATION ===================
# ================================================

# --- Output Redirection Class ---
class QueueRedirector:
    """A file-like object that redirects write calls to a queue."""
    def __init__(self, queue_instance):
        self.queue = queue_instance
        self._buffer = ''

    def write(self, text):
        """Writes text to the buffer and flushes lines to the queue."""
        # Don't process empty strings
        if not text:
            return
        self._buffer += text
        # Process lines ending with newline or carriage return
        while '\n' in self._buffer or '\r' in self._buffer:
            # Find the first newline or carriage return
            nl_pos = self._buffer.find('\n')
            cr_pos = self._buffer.find('\r')

            if nl_pos != -1 and (cr_pos == -1 or nl_pos < cr_pos):
                split_char = '\n'
                split_pos = nl_pos
            elif cr_pos != -1 and (nl_pos == -1 or cr_pos < nl_pos):
                 split_char = '\r'
                 split_pos = cr_pos
            else: # Should not happen if \n or \r is in buffer, but as fallback
                break

            line = self._buffer[:split_pos]
            self._buffer = self._buffer[split_pos+1:]

            if line: # Send non-empty lines to the queue
                 self.queue.put(('message', line.strip())) # Strip whitespace here

    def flush(self):
        """Flushes any remaining buffer content to the queue."""
        # Send any remaining buffer content when flush is called
        if self._buffer:
            self.queue.put(('message', self._buffer.strip())) # Strip whitespace
            self._buffer = ''

    def isatty(self): # Pretend not to be a tty
        return False

# --- GUI Application Class ---
class DownloaderApp:
    def __init__(self, root_window):
        self.root = root_window
        self.root.title("Modern MPD Downloader")
        # self.root.geometry("750x600") # Slightly larger default size

        # --- Style Configuration ---
        # Pick a theme: e.g., cosmo, flatly, journal, litera, lumen, minty, pulse, sandstone, united, yeti, morph, simplex, cerculean
        # Dark themes: darkly, solar, superhero, cyborg, vapor
        try:
            self.style = tb.Style(theme='vapor') # Example: vapor theme
        except tk.TclError:
            print("Warning: ttkbootstrap theme not found, using default.")
            self.style = ttk.Style() # Fallback to default ttk

        # --- Variables ---
        self.url_var = tk.StringVar()
        self.key_var = tk.StringVar()
        self.output_dir_var = tk.StringVar(value=os.path.abspath("."))
        self.is_running = False
        self.output_queue = queue.Queue()
        self.worker_thread = None # Keep track of the thread
        self.http_server = None # HTTP server instance
        self.http_server_thread = None # Thread for HTTP server
        self.shutting_down_event = threading.Event() # For graceful server shutdown

        # --- Build UI ---
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # Start periodic check of the queue
        self.check_queue_interval = 100 # ms
        self.root.after(self.check_queue_interval, self.process_queue)

        # Start the listener for the helper script
        self.start_helper_script_listener()

    def create_widgets(self):
        """Creates and lays out the GUI widgets."""
        main_frame = tb.Frame(self.root, padding="15")
        main_frame.pack(fill=BOTH, expand=YES)
        main_frame.columnconfigure(1, weight=1) # Input field column expands

        # --- Input Section ---
        input_frame = tb.Labelframe(main_frame, text=" Inputs ", padding=10, bootstyle="info")
        input_frame.grid(row=0, column=0, columnspan=3, padx=5, pady=5, sticky=EW)
        input_frame.columnconfigure(1, weight=1)

        tb.Label(input_frame, text="MPD URL:").grid(row=0, column=0, padx=5, pady=5, sticky=W)
        self.url_entry = tb.Entry(input_frame, textvariable=self.url_var, width=70)
        self.url_entry.grid(row=0, column=1, columnspan=2, padx=5, pady=5, sticky=EW)

        tb.Label(input_frame, text="Key (KID:KEY):").grid(row=1, column=0, padx=5, pady=5, sticky=W)
        self.key_entry = tb.Entry(input_frame, textvariable=self.key_var, width=70)
        self.key_entry.grid(row=1, column=1, columnspan=2, padx=5, pady=5, sticky=EW)

        # --- Output Section ---
        output_frame = tb.Labelframe(main_frame, text=" Output ", padding=10, bootstyle="info")
        output_frame.grid(row=1, column=0, columnspan=3, padx=5, pady=5, sticky=EW)
        output_frame.columnconfigure(1, weight=1)

        tb.Label(output_frame, text="Output Dir:").grid(row=0, column=0, padx=5, pady=5, sticky=W)
        self.dir_entry = tb.Entry(output_frame, textvariable=self.output_dir_var, state="readonly", width=60)
        self.dir_entry.grid(row=0, column=1, padx=5, pady=5, sticky=EW)
        self.browse_button = tb.Button(output_frame, text="Browse...", command=self.browse_directory, bootstyle="secondary-outline", width=10)
        self.browse_button.grid(row=0, column=2, padx=(5, 0), pady=5, sticky=W)

        # --- Action Button ---
        self.start_button = tb.Button(main_frame, text="Start Download", command=self.start_download_thread, bootstyle="success", width=20)
        self.start_button.grid(row=2, column=0, columnspan=3, padx=5, pady=15)

        # --- Status/Log Area ---
        log_frame = tb.Labelframe(main_frame, text=" Log ", padding=10, bootstyle="primary")
        log_frame.grid(row=3, column=0, columnspan=3, padx=5, pady=5, sticky=NSEW)
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        # Using tk.scrolledtext for scrollbar, styled background comes from Labelframe
        self.log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, height=18, width=90, state='disabled')
        self.log_text.grid(row=0, column=0, sticky=NSEW)
        # Use theme colors if possible
        try:
            info_fg = self.style.colors.fg
            success_fg = self.style.colors.success
            warning_fg = self.style.colors.warning
            error_fg = self.style.colors.danger
            step_fg = self.style.colors.primary
            dim_fg = self.style.colors.secondary
            progress_fg = self.style.colors.info # Specific color for progress
        except (AttributeError, tk.TclError): # Fallback if style or colors missing
            info_fg = "black"
            success_fg = "green"
            warning_fg = "orange"
            error_fg = "red"
            step_fg = "blue"
            dim_fg = "grey"
            progress_fg = "purple"

        self.log_text.tag_config('INFO', foreground=info_fg)
        self.log_text.tag_config('SUCCESS', foreground=success_fg, font=('helvetica', 9, 'bold'))
        self.log_text.tag_config('WARNING', foreground=warning_fg)
        self.log_text.tag_config('ERROR', foreground=error_fg, font=('helvetica', 9, 'bold'))
        self.log_text.tag_config('STEP', foreground=step_fg, font=('helvetica', 10, 'bold'))
        self.log_text.tag_config('DIM', foreground=dim_fg)
        self.log_text.tag_config('PROGRESS', foreground=progress_fg) # Tag for progress line

        main_frame.rowconfigure(3, weight=1) # Make log frame expandable

    def log_message(self, message, tag='INFO'):
        """Appends a message to the log text area safely from the main thread."""
        if not isinstance(message, str):
            message = str(message) # Ensure message is a string

        clean_message = re.sub(r'\x1b\[[0-9;]*m', '', message).strip() # Strip ANSI codes and leading/trailing whitespace
        if not clean_message:
            return # Don't log empty messages

        try:
            self.log_text.configure(state='normal')

            is_progress_update = (tag == 'PROGRESS')
            last_line_index = f"{int(self.log_text.index('end-1c').split('.')[0])}.0"
            current_content = self.log_text.get("1.0", tk.END).strip()
            ends_with_newline = current_content.endswith('\n') if current_content else True

            # Get tags of the very last character to see if it's a progress line
            last_char_index = self.log_text.index("end-2c") # Index before the final implicit newline
            is_last_line_progress = False
            if last_char_index != "1.0": # Check if not the first character
                 tags_on_last_char = self.log_text.tag_names(last_char_index)
                 is_last_line_progress = 'PROGRESS' in tags_on_last_char

            if is_progress_update:
                 if is_last_line_progress and current_content:
                     # Overwrite the previous progress line
                     self.log_text.delete(last_line_index, "end-1c")
                     # Insert the new progress line without an initial newline
                     self.log_text.insert('end', clean_message, ('PROGRESS',))
                 else:
                     # Add the first progress line or a progress line after a non-progress line
                     if not ends_with_newline and current_content:
                          self.log_text.insert('end', '\n')
                     self.log_text.insert('end', clean_message, ('PROGRESS',))
            else:
                # For regular messages, always start on a new line if needed
                if not ends_with_newline and current_content:
                    self.log_text.insert('end', '\n')
                elif is_last_line_progress: # Ensure newline after last progress msg
                    self.log_text.insert('end', '\n')

                self.log_text.insert('end', clean_message, (tag,))

                # Add a final newline for non-progress messages to ensure separation
                self.log_text.insert('end', '\n')


            self.log_text.see(tk.END) # Auto-scroll
        except Exception as e:
             print(f"GUI Error logging message: {e}") # Print direct error if logging fails
             traceback.print_exc()
        finally:
            self.log_text.configure(state='disabled')


    def browse_directory(self):
        """Opens a dialog to select the output directory."""
        selected_dir = filedialog.askdirectory(
            initialdir=self.output_dir_var.get(),
            title="Select Output Directory"
            )
        if selected_dir: # Only update if a directory was selected
            self.output_dir_var.set(os.path.abspath(selected_dir))

    def start_download_thread(self):
        """Validates inputs and starts the download process in a new thread."""
        if self.is_running:
            self.log_message("A download process is already running.", "WARNING")
            tk.messagebox.showwarning("Busy", "A download process is already running.")
            return

        url = self.url_var.get().strip()
        key = self.key_var.get().strip()
        output_dir = self.output_dir_var.get()

        # --- Input Validation ---
        errors = []
        if not url:
            errors.append("MPD URL cannot be empty.")
        if not key:
            errors.append("Key cannot be empty.")
        if key and ':' not in key:
            errors.append("Invalid key format. Expected KID:KEY.")
        if not output_dir:
             errors.append("Output directory cannot be empty.")
        elif not os.path.isdir(output_dir):
            errors.append(f"Output directory does not exist: {output_dir}")
        elif not os.access(output_dir, os.W_OK):
            errors.append(f"Output directory is not writable: {output_dir}")

        if errors:
            error_message = "Please fix the following errors:\n\n- " + "\n- ".join(errors)
            self.log_message("Input validation failed:", "ERROR")
            for err in errors: self.log_message(f"- {err}", "ERROR")
            tk.messagebox.showerror("Input Error", error_message)
            return

        # --- Disable UI elements ---
        self.is_running = True
        self.start_button.config(text="Running...", state=DISABLED, bootstyle="warning")
        self.url_entry.config(state=DISABLED)
        self.key_entry.config(state=DISABLED)
        self.dir_entry.config(state=DISABLED)
        self.browse_button.config(state=DISABLED)

        # --- Clear log ---
        self.log_text.configure(state='normal')
        self.log_text.delete('1.0', tk.END)
        self.log_text.configure(state='disabled')
        self.log_message("Starting download process...", "STEP")

        # --- Start background task ---
        self.worker_thread = threading.Thread(
            target=self.run_download_process,
            args=(url, key, output_dir),
            daemon=True # Allows closing app even if thread is running
        )
        self.worker_thread.start()
        # Restart queue checking if it stopped
        self.root.after(self.check_queue_interval, self.process_queue)

    def start_helper_script_listener(self):
        """Starts the HTTP server to listen for data from the helper script."""
        try:
            # Define a handler factory to pass the queue to each request handler instance
            # The server instance (self.http_server) will hold the queue.
            class CustomHelperScriptRequestHandler(HelperScriptRequestHandler):
                # This class inherits from HelperScriptRequestHandler defined globally
                # It doesn't need modification if HelperScriptRequestHandler uses self.server.app_queue
                pass

            self.http_server = HTTPServer(('', HELPER_SCRIPT_PORT), CustomHelperScriptRequestHandler)
            self.http_server.app_queue = self.output_queue # Make queue accessible to handlers
            self.http_server.shutting_down_event = self.shutting_down_event # For graceful shutdown

            self.http_server_thread = threading.Thread(target=self.run_http_server, daemon=True)
            self.http_server_thread.start()
            self.output_queue.put(('message', f"Helper script listener started on port {HELPER_SCRIPT_PORT}", "INFO"))
        except OSError as e:
            error_msg = f"Failed to start helper script listener on port {HELPER_SCRIPT_PORT}: {e}\nAnother application might be using this port."
            self.output_queue.put(('message', error_msg, "ERROR"))
            # Show a GUI error as this is a critical setup failure for the new feature
            try:
                tk.messagebox.showerror("Listener Error", error_msg, parent=self.root if self.root.winfo_exists() else None)
            except Exception: # In case root is not ready or gone
                print(f"GUI ERROR: {error_msg}", file=sys.stderr) # Fallback
        except Exception as e:
            self.output_queue.put(('message', f"Unexpected error starting helper script listener: {e}", "ERROR"))
            traceback.print_exc(file=sys.stderr)

    def run_http_server(self):
        """Runs the HTTP server loop, checking for shutdown signal."""
        # This method runs in self.http_server_thread
        if not self.http_server:
            return
        try:
            # self.http_server.serve_forever() # This blocks and is hard to shut down cleanly without shutdown() from another thread
            # Instead, use a loop with handle_request and a shutdown event
            while not self.shutting_down_event.is_set():
                self.http_server.handle_request() # Process one request or timeout
        except Exception as e:
            # Log error if server crashes unexpectedly, unless it's during shutdown
            if not self.shutting_down_event.is_set():
                self.output_queue.put(('message', f"Helper script listener crashed: {e}", "ERROR"))
                traceback.print_exc(file=sys.stderr)
        finally:
            if self.http_server:
                self.http_server.server_close() # Ensure socket is closed
            self.output_queue.put(('message', "Helper script listener has stopped.", "INFO"))


    def _shutdown_http_server(self):
        """Gracefully shuts down the HTTP server."""
        if self.http_server and self.http_server_thread and self.http_server_thread.is_alive():
            self.output_queue.put(('message', "Shutting down helper script listener...", "INFO"))
            self.shutting_down_event.set() # Signal the server loop to stop
            try:
                # To unblock handle_request if it's waiting, send a dummy request to the server
                # This is a common technique for servers not using select() with timeout in handle_request
                # Or, rely on the fact that handle_request might time out or the app is closing anyway.
                # For simplicity, we'll rely on the app closing or next natural timeout of handle_request.
                self.http_server.server_close() # Close the server socket immediately
                self.http_server_thread.join(timeout=1.0) # Wait for the thread to finish
                if self.http_server_thread.is_alive():
                    self.output_queue.put(('message', "Helper script listener thread did not exit cleanly.", "WARNING"))
            except Exception as e:
                self.output_queue.put(('message', f"Error during helper listener shutdown: {e}", "ERROR"))
                traceback.print_exc(file=sys.stderr)
        self.http_server = None
        self.http_server_thread = None


    def run_download_process(self, url, key, output_dir):
        """The function that runs in the background thread."""
        redirector = QueueRedirector(self.output_queue)
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        # Redirect stdout and stderr FOR THIS THREAD ONLY
        sys.stdout = redirector
        sys.stderr = redirector

        success = False
        final_file_path = None
        downloaded_video, decrypted_video = None, None
        downloaded_audio, decrypted_audio = None, None

        try:
            # --- Define the GUI-specific progress hook ---
            def gui_download_progress_hook(d):
                # This hook runs within the worker thread but puts messages on the queue
                # for the GUI thread to process.
                if d['status'] == 'downloading':
                    percent_str = d.get('_percent_str', '---%')
                    speed_str = d.get('_speed_str', '--- B/s')
                    eta_str = d.get('_eta_str', '--:--')
                    fragment_index = d.get('fragment_index')
                    fragment_count = d.get('fragment_count')

                    if fragment_index and fragment_count:
                         progress_line = f"[download] Fragment {fragment_index}/{fragment_count} "
                         progress_line += f"({percent_str.strip():>6}) at {speed_str.strip():>10} ETA {eta_str.strip()}"
                    else:
                         progress_line = f"[download] {percent_str.strip():>6} "
                         progress_line += f"at {speed_str.strip():>10} ETA {eta_str.strip()}"

                    # Put raw progress string onto queue, tagged as PROGRESS
                    # Don't use print() here, directly queue it
                    self.output_queue.put(('message', progress_line, 'PROGRESS'))

                elif d['status'] == 'finished':
                    # Queue a finished message using the standard print redirect
                    size_str = f"{d.get('_total_bytes_str', 'N/A')}"
                    elapsed_str = f"{d.get('elapsed'):.2f}s" if d.get('elapsed') else "N/A"
                    speed_str = f"{d.get('_speed_str', 'N/A')}" if d.get('speed') else "N/A"
                    # Use the core print_success function, which will go via redirector
                    print_success(f"Downloaded '{os.path.basename(d.get('filename', 'file'))}' ({size_str}) in {elapsed_str} (Avg speed: {speed_str})")

                elif d['status'] == 'error':
                     # Use the core print_error function via redirector
                     print_error(f"Download error occurred for '{d.get('filename', 'file')}'")
                     # yt-dlp might also raise an exception which we'll catch below

            # === Start of Core Logic Execution ===

            # Step 0: Check tools (prints via redirector)
            if not check_tool(MP4DECRYPT_PATH, "mp4decrypt") or not check_tool(FFMPEG_PATH, "ffmpeg"):
                raise RuntimeError("Required external tools (mp4decrypt or ffmpeg) not found or configured.")

            # Step 1: Generate Filename (prints via redirector)
            raw_filename = generate_filename(url)
            base_filename = sanitize_filename(raw_filename)
            if "error" in base_filename or not base_filename:
                raise RuntimeError("Could not generate a valid base filename from the URL.")
            print_info(f"Using base filename: {base_filename}")
            base_filepath_sans_ext = os.path.join(output_dir, base_filename)
            output_template_abs = os.path.join(output_dir, f"{base_filename}.%(ext)s")

            # Step 2: Download (using the modified function and GUI hook)
            if not download_video_library(url, output_template_abs, gui_download_progress_hook):
                 # Error messages should have been printed by the hook or download function itself
                 raise RuntimeError("Download failed (yt-dlp reported errors).")

            # Step 3: Find Downloaded Files (prints via redirector)
            downloaded_video, downloaded_audio = find_downloaded_files(base_filepath_sans_ext)
            if not downloaded_video or not downloaded_audio:
                raise RuntimeError("Failed to find necessary downloaded files after download completed.")

            # Step 4: Decrypt Files (prints via redirector)
            decrypted_video, decrypted_audio = decrypt_files(key, downloaded_video, downloaded_audio)
            if not decrypted_video or not decrypted_audio:
                raise RuntimeError("File decryption failed.")

            # Step 5: Merge Files (prints via redirector)
            final_output_file = f"{base_filepath_sans_ext}_final.mp4"
            if not merge_files(decrypted_video, decrypted_audio, final_output_file):
                raise RuntimeError("Merging decrypted files failed.")

            # --- Success ---
            final_file_path = os.path.abspath(final_output_file)
            success = True
            print_step("Process Complete")
            print_success("Final video saved successfully:")
            # Print final path for clarity (goes to log via redirector)
            print(f"{final_file_path}") # Simple print for the path

        except Exception as e:
            # Log the exception using the core print_error via redirector
            print_error(f"An unexpected error occurred: {e}")
            # Also print traceback via redirector for debugging
            traceback.print_exc(file=sys.stderr)
            success = False
            final_file_path = None # Ensure no final path if failed
        finally:
            # === Cleanup within the thread ===
            # Restore stdout/stderr
            sys.stdout = original_stdout
            sys.stderr = original_stderr

            # Optional: Clean up intermediate files ONLY IF successful
            files_to_delete = []
            if success:
                if downloaded_video and os.path.exists(downloaded_video): files_to_delete.append(downloaded_video)
                # Only add audio if it's a different file
                if downloaded_audio and os.path.exists(downloaded_audio) and os.path.normpath(downloaded_audio) != os.path.normpath(downloaded_video):
                     files_to_delete.append(downloaded_audio)
                if decrypted_video and os.path.exists(decrypted_video): files_to_delete.append(decrypted_video)
                # Only add decrypted audio if it's different
                if decrypted_audio and os.path.exists(decrypted_audio) and os.path.normpath(decrypted_audio) != os.path.normpath(decrypted_video):
                     files_to_delete.append(decrypted_audio)

                if files_to_delete:
                    print_step("Cleaning up temporary files...") # Use print_step via QueueRedirector
                    for f_path in files_to_delete:
                        try:
                             print_info(f"Deleting: {os.path.basename(f_path)}") # Goes to queue
                             os.remove(f_path)
                        except OSError as err:
                             print_warning(f"Could not delete temporary file {os.path.basename(f_path)}: {err}") # Goes to queue
                    print_success("Cleanup complete.")
            else:
                # If failed, print info about temporary files
                print_warning("Process failed. Intermediate files were kept for inspection:")
                temp_files = []
                if downloaded_video and os.path.exists(downloaded_video): temp_files.append(downloaded_video)
                if downloaded_audio and os.path.exists(downloaded_audio) and os.path.normpath(downloaded_audio) != os.path.normpath(downloaded_video): temp_files.append(downloaded_audio)
                if decrypted_video and os.path.exists(decrypted_video): temp_files.append(decrypted_video)
                if decrypted_audio and os.path.exists(decrypted_audio) and os.path.normpath(decrypted_audio) != os.path.normpath(decrypted_video): temp_files.append(decrypted_audio)
                for f_path in temp_files: print_info(f"- {f_path}")


            # --- Signal GUI thread that processing is finished ---
            # This put operation MUST happen after restoring stdout/stderr
            # payload = (success, final_file_path)
            self.output_queue.put(('finished', success, final_file_path))

    def process_queue(self):
        """Checks the queue for messages and updates the GUI log."""
        try:
            while True: # Process all available messages
                msg_type, *payload = self.output_queue.get_nowait()

                if msg_type == 'message':
                    message = payload[0]
                    # Determine tag based on payload or message content heuristic
                    tag = 'INFO' # Default tag
                    if len(payload) > 1 and payload[1] == 'PROGRESS':
                        tag = 'PROGRESS'
                    else:
                        # Heuristic based on core print functions' output patterns
                        # (stripping potential color codes isn't strictly necessary here
                        # as log_message does it, but helps clarity)
                        clean_msg_for_tag = re.sub(r'\x1b\[[0-9;]*m', '', str(message)).strip()
                        if clean_msg_for_tag.startswith(">>>>>"): tag = 'STEP'
                        elif clean_msg_for_tag.startswith("✔"): tag = 'SUCCESS'
                        elif clean_msg_for_tag.startswith("⚠"): tag = 'WARNING'
                        elif clean_msg_for_tag.startswith("✖ ERROR:"): tag = 'ERROR'
                        elif clean_msg_for_tag.startswith("Running command:"): tag = 'DIM'
                        # PROGRESS is handled explicitly by the payload check above

                    # Update GUI log (runs in GUI thread)
                    self.log_message(message, tag)

                elif msg_type == 'finished':
                    success, final_path = payload
                    # Call the finish handler (runs in GUI thread)
                    self.on_process_finished(success, final_path)

                elif msg_type == 'update_inputs':
                    mpd_url, key_data = payload
                    self.url_var.set(mpd_url)
                    self.key_var.set(key_data)
                    self.log_message(f"Inputs auto-filled by helper script:", "SUCCESS")
                    self.log_message(f"  MPD URL: {mpd_url}", "INFO")
                    self.log_message(f"  Key: {key_data}", "INFO")
                self.output_queue.task_done() # Mark message as processed

        except queue.Empty:
            # No messages currently in queue
            pass
        except Exception as e:
             # Error processing queue item
             print(f"GUI Error processing queue: {e}")
             traceback.print_exc()
        finally:
            # Reschedule the check ONLY if the root window still exists
            if self.root.winfo_exists():
                 # Check again after interval
                 self.root.after(self.check_queue_interval, self.process_queue)


    def on_process_finished(self, success, final_file_path):
        """Called in the GUI thread when the background process completes."""
        self.is_running = False
        # Re-enable UI elements
        try:
            self.start_button.config(text="Start Download", state=NORMAL, bootstyle="success")
            self.url_entry.config(state=NORMAL)
            self.key_entry.config(state=NORMAL)
            self.dir_entry.config(state=NORMAL) # Keep readonly if needed
            self.browse_button.config(state=NORMAL)
        except tk.TclError:
             print("Warning: Could not re-enable GUI elements (window likely closing).") # Ignore if widgets are gone

        if success and final_file_path:
             self.log_message(f"\n--- Process Finished Successfully ---", "SUCCESS")
             # Optionally ask to open directory
             if tk.messagebox.askyesno("Success", f"Download successful!\nFinal file: {os.path.basename(final_file_path)}\n\nOpen the output directory?", parent=self.root):
                  try:
                       output_dir = os.path.dirname(final_file_path)
                       if sys.platform == "win32":
                            os.startfile(output_dir)
                       elif sys.platform == "darwin": # macOS
                            subprocess.Popen(["open", output_dir])
                       else: # Linux and other POSIX
                            subprocess.Popen(["xdg-open", output_dir])
                  except Exception as e:
                       self.log_message(f"Could not open output directory automatically: {e}", "WARNING")
                       tk.messagebox.showwarning("Open Directory", f"Could not open output directory automatically:\n{e}", parent=self.root)
        else:
            self.log_message(f"\n--- Process Failed ---", "ERROR")
            tk.messagebox.showerror("Failed", "The download process failed. Please check the log for details.", parent=self.root)

        self.worker_thread = None # Clear worker thread reference


    def on_close(self):
        """Handle window closing actions."""
        if self.is_running:
            # Ask user if they want to quit anyway
            if tk.messagebox.askyesno("Confirm Exit", "A download is currently in progress.\nAre you sure you want to exit?\nThe process may not complete cleanly.", parent=self.root):
                 self.output_queue.put(('message', "WARN: Exiting while process is running!", "WARNING"))
                 # No foolproof way to kill the subprocesses started by yt-dlp/ffmpeg reliably cross-platform
                 # The daemon thread will exit when the main app exits, but subprocesses might linger
                 self._shutdown_http_server() # Attempt to shutdown server
                 self.root.destroy()
            else:
                return # Don't close if user cancels
        else:
             self._shutdown_http_server() # Shutdown server
             self.root.destroy() # Close immediately if not running

# --- HTTP Request Handler for Helper Script ---
class HelperScriptRequestHandler(BaseHTTPRequestHandler):
    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*") # Allow all origins for local helper
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204) # No Content for preflight
        self._send_cors_headers()
        self.end_headers()

    def do_POST(self):
        if self.path == '/submit_data':
            try:
                content_length = int(self.headers['Content-Length'])
                post_data_bytes = self.rfile.read(content_length)
                data = json.loads(post_data_bytes.decode('utf-8'))

                mpd_url = data.get('mpdUrl')
                key = data.get('key')

                if mpd_url and key:
                    self.server.app_queue.put(('update_inputs', mpd_url, key))
                    self.send_response(200)
                    self._send_cors_headers()
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "success", "message": "Data received"}).encode('utf-8'))
                    self.server.app_queue.put(('message', f"Helper script: Received MPD URL and Key.", 'INFO'))
                else:
                    self.send_response(400) # Bad Request
                    self._send_cors_headers()
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "error", "message": "Missing mpdUrl or key"}).encode('utf-8'))
                    self.server.app_queue.put(('message', f"Helper script: Received incomplete data: {data}", 'WARNING'))
            except Exception as e:
                self.send_response(500) # Internal Server Error
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": f"Server error: {str(e)}"}).encode('utf-8'))
                self.server.app_queue.put(('message', f"Helper script: Error processing request: {e}", 'ERROR'))
                traceback.print_exc(file=sys.stderr) # Goes to app queue via stderr redirection
        else:
            self.send_response(404) # Not Found
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"status": "error", "message": "Endpoint not found"}).encode('utf-8'))

# ================================================
# ========= MAIN EXECUTION BLOCK =================
# ================================================
if __name__ == "__main__":
    # --- IMPORTANT ---
    # Disable direct color printing from core functions when running GUI
    # The GUI log handles colors/styles based on tags.
    USE_COLOR = False

    # --- Set up and run the GUI ---
    # Use themed window if available
    try:
        root = tb.Window(themename='vapor') # Use the same theme name as in the class
    except tk.TclError:
        root = tk.Tk() # Fallback if theme fails

    app = DownloaderApp(root)
    root.mainloop()