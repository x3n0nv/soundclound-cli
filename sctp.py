"""
SoundCloud Terminal Player (sctp)
Requires:
- Python 3.10+
- Textual
- python-vlc
- beautifulsoup4
- requests
- kitty terminal (do wyświetlania obrazów)
"""

import os
import re
import time
import subprocess
from typing import List, Dict, Optional
from dataclasses import dataclass
from functools import lru_cache

import requests
from bs4 import BeautifulSoup
import vlc
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Container, VerticalScroll
from textual.widgets import Input, Button, Static, ListView, ListItem, Label
from textual.binding import Binding
from textual.worker import get_current_worker

# ----------
# Data Models
# ----------

@dataclass
class Track:
    """Reprezentacja pojedynczego utworu"""
    title: str
    artist: str
    duration: int
    stream_url: str
    artwork_url: str
    id: str

# ----------
# SoundCloud Scraper
# ----------

class SoundCloudScraper:
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }

    @classmethod
    @lru_cache(maxsize=100)
    def search(cls, query: str) -> List[Track]:
        """Wyszukuje utwory w SoundCloud przez scraping"""
        try:
            url = f"https://soundcloud.com/search/sounds?q={query}"
            response = requests.get(url, headers=cls.HEADERS, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, "html.parser")
            tracks = []
            
            for item in soup.select("li.searchList__item"):
                # Parsowanie danych utworu (pseudo-kod - potrzebne dostosowanie selektorów)
                title = item.select_one("a.soundTitle__title").text.strip()
                artist = item.select_one("a.soundTitle__username").text.strip()
                duration = cls.parse_duration(item.select_one("span.sc-visuallyhidden").text)
                track_id = item.select_one("a")["href"].split("/")[-1]
                
                tracks.append(Track(
                    title=title,
                    artist=artist,
                    duration=duration,
                    stream_url=f"https://api.soundcloud.com/tracks/{track_id}/stream",  # Wymaga aktualizacji
                    artwork_url=item.select_one("img")["src"],
                    id=track_id
                ))
            
            return tracks
        except Exception as e:
            # Logowanie błędów
            return []

    @staticmethod
    def parse_duration(duration_str: str) -> int:
        """Konwertuje czas w formacie MM:SS na sekundy"""
        parts = list(map(int, duration_str.split(":")))
        return parts[0] * 60 + parts[1]

# ----------
# Audio Player
# ----------

class AudioPlayer:
    def __init__(self):
        self.instance = vlc.Instance("--no-xlib")
        self.player = self.instance.media_player_new()
        self.current_track: Optional[Track] = None

    def play(self, track: Track) -> None:
        """Rozpoczyna odtwarzanie utworu"""
        media = self.instance.media_new(track.stream_url)
        self.player.set_media(media)
        self.player.play()
        self.current_track = track

    def toggle_pause(self) -> None:
        """Wstrzymuje/wznawia odtwarzanie"""
        self.player.pause()

    def stop(self) -> None:
        """Zatrzymuje odtwarzanie"""
        self.player.stop()

# ----------
# UI Components
# ----------

class TrackWidget(ListItem):
    """Widget reprezentujący pojedynczy utwór w liście"""
    def __init__(self, track: Track):
        super().__init__()
        self.track = track
        self.add_class("track-item")

    def compose(self) -> ComposeResult:
        yield Label(f"{self.track.artist} - {self.track.title}")
        yield Label(time.strftime("%M:%S", time.gmtime(self.track.duration)))

class Artwork(Static):
    """Widget wyświetlający okładkę w terminalu Kitty"""
    def __init__(self, url: str):
        super().__init__()
        self.url = url

    def on_mount(self) -> None:
        self.display_image()

    def display_image(self) -> None:
        """Wyświetla obrazek używając protokołu Kitty"""
        if "KITTY_WINDOW_ID" in os.environ:
            subprocess.run([
                "kitty", "+kitten", "icat",
                "--align", "left",
                "--place", "30x30@0x0",
                self.url
            ])

# ----------
# Main Application
# ----------

class SCTP(App):
    CSS_PATH = "style.css"
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("space", "play_pause", "Play/Pause"),
        Binding("n", "next_track", "Next"),
        Binding("p", "prev_track", "Previous"),
    ]

    def __init__(self):
        super().__init__()
        self.scraper = SoundCloudScraper()
        self.player = AudioPlayer()
        self.current_tracks: List[Track] = []
        self.selected_track: Optional[Track] = None

    def compose(self) -> ComposeResult:
        """Układ głównego interfejsu"""
        yield Input(placeholder="Search SoundCloud...", id="search-input")
        yield Button("Search", id="search-btn")
        yield Container(
            VerticalScroll(ListView(id="track-list")),
            Artwork("", id="artwork"),
            id="main-container"
        )

    # Event Handlers
    @on(Input.Submitted, "#search-input")
    @on(Button.Pressed, "#search-btn")
    def handle_search(self) -> None:
        """Obsługa wyszukiwania"""
        query = self.query_one("#search-input", Input).value
        if query:
            self.search_tracks(query)

    @on(ListView.Selected)
    def handle_track_select(self, event: ListView.Selected) -> None:
        """Obsługa wyboru utworu z listy"""
        if isinstance(event.item, TrackWidget):
            self.selected_track = event.item.track
            self.play_selected_track()

    def play_selected_track(self) -> None:
        """Rozpocznij odtwarzanie wybranego utworu"""
        if self.selected_track:
            self.player.play(self.selected_track)
            self.update_artwork(self.selected_track.artwork_url)

    # Methods
    @work(exclusive=True)
    def search_tracks(self, query: str) -> None:
        """Wyszukaj utwory (asynchronicznie)"""
        worker = get_current_worker()
        self.notify(f"Searching for '{query}'...")
        
        tracks = self.scraper.search(query)
        if not worker.is_cancelled:
            self.current_tracks = tracks
            self.update_track_list(tracks)

    def update_track_list(self, tracks: List[Track]) -> None:
        """Aktualizuje listę wyników w UI"""
        list_view = self.query_one("#track-list", ListView)
        list_view.clear()
        
        for track in tracks:
            list_view.append(TrackWidget(track))

    def update_artwork(self, url: str) -> None:
        """Aktualizuje okładkę utworu"""
        artwork = self.query_one("#artwork", Artwork)
        artwork.url = url
        artwork.display_image()

    # Actions
    def action_play_pause(self) -> None:
        """Obsługa play/pause"""
        self.player.toggle_pause()

if __name__ == "__main__":
    app = SCTP()
    app.run()