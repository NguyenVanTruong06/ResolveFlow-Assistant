import urllib.request
import json
import urllib.parse
import os
import subprocess
import tempfile

FILES = {
    'chill_vlog': ('Kevin MacLeod - Carefree.ogg', 'Carefree.mp3'),
    'funny': ('Sneaky Snitch by Kevin MacLeod.ogg', 'Sneaky_Snitch.mp3'),
    'cinematic': ('Kevin MacLeod - Gustav Holst Thaxted.oga', 'Gustav_Holst_Thaxted.mp3'),
    'upbeat_trend': ('Life of Riley (ISRC USUAN1400054).mp3', 'Life_of_Riley.mp3'),
}

def get_wikimedia_url(filename):
    url = f"https://en.wikipedia.org/w/api.php?action=query&titles=File:{urllib.parse.quote(filename)}&prop=imageinfo&iiprop=url&format=json"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as r:
        data = json.load(r)
        pages = data['query']['pages']
        for page_id in pages:
            if 'imageinfo' in pages[page_id]:
                return pages[page_id]['imageinfo'][0]['url']
    
    # Try commons directly
    url = f"https://commons.wikimedia.org/w/api.php?action=query&titles=File:{urllib.parse.quote(filename)}&prop=imageinfo&iiprop=url&format=json"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as r:
        data = json.load(r)
        pages = data['query']['pages']
        for page_id in pages:
            if 'imageinfo' in pages[page_id]:
                return pages[page_id]['imageinfo'][0]['url']
    return None

def download_and_convert():
    base_dir = os.path.join(os.getcwd(), 'assets', 'music')
    for category, (wiki_file, out_name) in FILES.items():
        print(f"Processing {category} -> {wiki_file}")
        url = get_wikimedia_url(wiki_file)
        if not url:
            print(f"Could not find URL for {wiki_file}")
            continue
            
        print(f"Downloading from {url}...")
        
        # Download to temp file
        ext = wiki_file.split('.')[-1]
        temp_in = os.path.join(tempfile.gettempdir(), f"temp_dl.{ext}")
        
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response, open(temp_in, 'wb') as out_file:
            out_file.write(response.read())
            
        out_path = os.path.join(base_dir, category, out_name)
        
        # Remove old mock wav files
        for old_file in os.listdir(os.path.join(base_dir, category)):
            if old_file.endswith('.wav') or old_file.endswith('.mp3'):
                try:
                    os.remove(os.path.join(base_dir, category, old_file))
                except:
                    pass
        
        print(f"Converting {out_name}...")
        subprocess.run(['ffmpeg', '-y', '-i', temp_in, '-vn', '-acodec', 'libmp3lame', '-q:a', '2', out_path], 
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Cleanup
        try:
            os.remove(temp_in)
        except:
            pass
        
        print(f"Successfully saved {out_name} to {category}\n")

if __name__ == '__main__':
    download_and_convert()
