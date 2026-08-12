"""
local_settings.py — SIRF VPS pe rehti hai. .gitignore mein excluded hai,
GitHub pe kabhi push nahi hoti. Har baar naye VPS pe deploy karte waqt ye
file manually banani padegi (scp/nano se), repo pull karne se nahi aayegi.
"""

SUPABASE_URL = "https://YOUR-PROJECT.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = "PASTE_SERVICE_ROLE_KEY_HERE"
