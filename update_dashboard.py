#!/usr/bin/env python3
"""
GitHub README Dashboard Updater for Strava and Sleep Data
Updates the README with running stats and sleep data from Strava and Garmin Connect
"""

import os
import re
import json
import time
import base64
import requests
import datetime
import urllib.parse
from dateutil import parser
from dateutil.relativedelta import relativedelta
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.colors import LinearSegmentedColormap
import pandas as pd
import numpy as np
import polyline
from staticmap import StaticMap, Line, CircleMarker
from geopy.distance import geodesic
import io
import logging
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration
STRAVA_API_URL = "https://www.strava.com/api/v3"
GARMIN_API_URL = "https://connect.garmin.com/modern/proxy/usersummary-service/usersummary"
STRAVA_ORANGE = "#FC4C02"
BACKGROUND_COLOR = "#0D1117"  # GitHub dark mode background
TEXT_COLOR = "#E6EDF3"  # GitHub dark mode text
SECONDARY_COLOR = "#58a6ff"  # GitHub accent blue
IMAGES_DIR = Path("images")

# Ensure images directory exists
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

class StravaClient:
    def __init__(self):
        self.client_id = os.environ.get("STRAVA_CLIENT_ID")
        self.client_secret = os.environ.get("STRAVA_CLIENT_SECRET")
        self.refresh_token = os.environ.get("STRAVA_REFRESH_TOKEN")
        self.access_token = None
        self.expires_at = 0

    def get_access_token(self):
        """Get a new access token using the refresh token"""
        if self.access_token and time.time() < self.expires_at:
            return self.access_token

        url = "https://www.strava.com/oauth/token"
        payload = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'refresh_token': self.refresh_token,
            'grant_type': 'refresh_token'
        }
        
        try:
            response = requests.post(url, data=payload)
            response.raise_for_status()
            token_data = response.json()
            self.access_token = token_data['access_token']
            self.expires_at = token_data['expires_at']
            return self.access_token
        except requests.exceptions.RequestException as e:
            logger.error(f"Error refreshing Strava token: {e}")
            raise

    def get_athlete(self):
        """Get current athlete information"""
        url = f"{STRAVA_API_URL}/athlete"
        headers = self._get_headers()
        
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error getting athlete data: {e}")
            raise

    def get_activities(self, per_page=10):
        """Get recent activities"""
        url = f"{STRAVA_API_URL}/athlete/activities"
        headers = self._get_headers()
        params = {'per_page': per_page}
        
        try:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error getting activities: {e}")
            raise

    def get_weekly_stats(self, weeks=12):
        """Get weekly running stats for the last X weeks"""
        end_date = datetime.datetime.now()
        start_date = end_date - datetime.timedelta(weeks=weeks)
        
        activities = []
        page = 1
        per_page = 100
        
        while True:
            url = f"{STRAVA_API_URL}/athlete/activities"
            headers = self._get_headers()
            params = {
                'before': int(end_date.timestamp()),
                'after': int(start_date.timestamp()),
                'page': page,
                'per_page': per_page
            }
            
            try:
                response = requests.get(url, headers=headers, params=params)
                response.raise_for_status()
                page_activities = response.json()
                
                if not page_activities:
                    break
                    
                activities.extend(page_activities)
                page += 1
            except requests.exceptions.RequestException as e:
                logger.error(f"Error getting weekly stats: {e}")
                break
        
        # Only running activities
        running_activities = [a for a in activities if a['type'] == 'Run']
        
        # Group by week
        weekly_data = {}
        for activity in running_activities:
            start_date = parser.parse(activity['start_date'])
            week_start = (start_date - datetime.timedelta(days=start_date.weekday())).strftime('%Y-%m-%d')
            
            if week_start not in weekly_data:
                weekly_data[week_start] = {
                    'distance': 0,
                    'count': 0,
                    'time': 0,
                    'elevation': 0
                }
                
            weekly_data[week_start]['distance'] += activity['distance'] / 1609.34  # Convert to miles
            weekly_data[week_start]['count'] += 1
            weekly_data[week_start]['time'] += activity['moving_time']
            weekly_data[week_start]['elevation'] += activity.get('total_elevation_gain', 0) * 3.28084  # Convert to feet
        
        # Convert to list sorted by date
        result = [{'week': k, **v} for k, v in weekly_data.items()]
        result.sort(key=lambda x: x['week'])
        
        return result

    def get_ytd_stats(self):
        """Get year-to-date running stats"""
        url = f"{STRAVA_API_URL}/athletes/{self.get_athlete()['id']}/stats"
        headers = self._get_headers()
        
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            return response.json().get('ytd_run_totals', {})
        except requests.exceptions.RequestException as e:
            logger.error(f"Error getting YTD stats: {e}")
            raise

    def get_recent_race(self):
        """Find the most recent race from activities"""
        activities = self.get_activities(per_page=50)
        
        # Look for races (activities with "race" in the name or description)
        races = []
        for activity in activities:
            name = activity.get('name', '').lower()
            description = activity.get('description', '').lower() if activity.get('description') else ''
            
            race_keywords = ['race', 'marathon', '5k', '10k', 'half-marathon', 'half marathon', 'ultra']
            if any(keyword in name for keyword in race_keywords) or any(keyword in description for keyword in race_keywords):
                races.append(activity)
        
        if not races:
            return None
            
        # Get the most recent race with details
        latest_race = races[0]
        race_id = latest_race['id']
        
        url = f"{STRAVA_API_URL}/activities/{race_id}"
        headers = self._get_headers()
        
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            race_details = response.json()
            return race_details
        except requests.exceptions.RequestException as e:
            logger.error(f"Error getting race details: {e}")
            return latest_race  # Return basic info if detailed fetch fails

    def get_personal_records(self):
        """Get personal records for common distances"""
        activities = self.get_activities(per_page=100)
        running_activities = [a for a in activities if a['type'] == 'Run']
        
        # Define standard distances in meters
        standard_distances = {
            '1 mile': 1609.34,
            '5K': 5000,
            '10K': 10000,
            'Half Marathon': 21097.5,
            'Marathon': 42195
        }
        
        # Initialize records dictionary
        records = {distance: {'time': float('inf'), 'activity': None} for distance in standard_distances}
        
        # For each activity, check if it's close to a standard distance and if it's a PR
        for activity in running_activities:
            distance = activity['distance']  # meters
            elapsed_time = activity['elapsed_time']  # seconds
            
            for distance_name, distance_meters in standard_distances.items():
                # Allow some margin for GPS inaccuracy (within 2%)
                if abs(distance - distance_meters) / distance_meters <= 0.02:
                    if elapsed_time < records[distance_name]['time']:
                        records[distance_name]['time'] = elapsed_time
                        records[distance_name]['activity'] = activity
        
        # Format the results
        formatted_records = []
        for distance_name, record in records.items():
            if record['activity']:
                activity = record['activity']
                formatted_records.append({
                    'distance': distance_name,
                    'time': self._format_time(record['time']),
                    'pace': self._calculate_pace(record['time'], activity['distance']),
                    'date': parser.parse(activity['start_date_local']).strftime('%Y-%m-%d'),
                    'location': self._get_location(activity)
                })
        
        return formatted_records

    def _get_headers(self):
        """Get headers with access token for API requests"""
        return {'Authorization': f'Bearer {self.get_access_token()}'}

    def _format_time(self, seconds):
        """Format seconds to HH:MM:SS"""
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        if hours > 0:
            return f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}"
        else:
            return f"{int(minutes):02d}:{int(seconds):02d}"

    def _calculate_pace(self, seconds, meters):
        """Calculate pace in minutes per mile"""
        miles = meters / 1609.34
        if miles <= 0:
            return "0:00/mi"
        
        pace_seconds = seconds / miles
        pace_minutes, pace_remainder = divmod(pace_seconds, 60)
        
        return f"{int(pace_minutes)}:{int(pace_remainder):02d}/mi"

    def _get_location(self, activity):
        """Extract location from activity"""
        if activity.get('location_city') and activity.get('location_state'):
            return f"{activity['location_city']}, {activity['location_state']}"
        elif activity.get('location_city'):
            return activity['location_city']
        elif activity.get('location_state'):
            return activity['location_state']
        elif activity.get('location_country'):
            return activity['location_country']
        else:
            return "Unknown location"


class GarminClient:
    def __init__(self):
        self.username = os.environ.get("GARMIN_USERNAME")
        self.password = os.environ.get("GARMIN_PASSWORD")
        self.session = requests.Session()
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
    def login(self):
        """Log in to Garmin Connect"""
        auth_url = "https://sso.garmin.com/sso/signin"
        
        # First request to get CSRF token
        params = {
            'service': 'https://connect.garmin.com/modern',
            'webhost': 'https://connect.garmin.com',
            'source': 'https://connect.garmin.com/en-US/signin',
            'redirectAfterAccountLoginUrl': 'https://connect.garmin.com/modern',
            'redirectAfterAccountCreationUrl': 'https://connect.garmin.com/modern',
            'gauthHost': 'https://sso.garmin.com/sso',
            'locale': 'en_US',
            'id': 'gauth-widget',
            'cssUrl': 'https://static.garmincdn.com/com.garmin.connect/ui/css/gauth-custom-v1.2-min.css',
            'clientId': 'GarminConnect',
            'rememberMeShown': 'true',
            'rememberMeChecked': 'false',
            'createAccountShown': 'true',
            'openCreateAccount': 'false',
            'displayNameShown': 'false',
            'consumeServiceTicket': 'false',
            'initialFocus': 'true',
            'embedWidget': 'false',
            'generateExtraServiceTicket': 'true',
            'generateTwoExtraServiceTickets': 'false',
            'generateNoServiceTicket': 'false',
            'globalOptInShown': 'true',
            'globalOptInChecked': 'false',
            'mobile': 'false',
            'connectLegalTerms': 'true',
            'locationPromptShown': 'true',
            'showPassword': 'true'
        }
        
        try:
            response = self.session.get(auth_url, params=params, headers=self.headers)
            response.raise_for_status()
            
            # Extract CSRF token
            csrf = None
            match = re.search(r'name="_csrf"\s+value="(\w+)"', response.text)
            if match:
                csrf = match.group(1)
            else:
                logger.error("Could not find CSRF token")
                return False
                
            # Login with username and password
            data = {
                'username': self.username,
                'password': self.password,
                'embed': 'false',
                '_csrf': csrf
            }
            
            response = self.session.post(auth_url, params=params, data=data, headers=self.headers, allow_redirects=False)
            
            if response.status_code != 302:
                logger.error("Login failed - no redirect received")
                return False
                
            # Follow redirects manually to get tickets
            redirect_url = response.headers.get('Location')
            response = self.session.get(redirect_url, headers=self.headers, allow_redirects=False)
            
            # Continue following redirects as needed
            while response.status_code == 302:
                redirect_url = response.headers.get('Location')
                response = self.session.get(redirect_url, headers=self.headers, allow_redirects=False)
            
            if 'SESSIONID' in self.session.cookies.get_dict():
                logger.info("Garmin login successful")
                return True
            else:
                logger.error("Garmin login failed - no session cookie")
                return False
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Error during Garmin login: {e}")
            return False

    def get_sleep_data(self, days=14):
        """Get sleep data for the last X days"""
        if not self.login():
            return []
            
        sleep_data = []
        end_date = datetime.datetime.now()
        start_date = end_date - datetime.timedelta(days=days)
        
        # Format dates for API
        start_date_str = start_date.strftime('%Y-%m-%d')
        end_date_str = end_date.strftime('%Y-%m-%d')
        
        url = f"{GARMIN_API_URL}/sleep/daily/{start_date_str}/{end_date_str}"
        
        try:
            response = self.session.get(url, headers=self.headers)
            response.raise_for_status()
            data = response.json()
            
            for day in data:
                if day.get('sleepScores') and day.get('dailySleepDTO'):
                    overall_score = day['sleepScores'].get('overall', {}).get('value', 0)
                    sleep_seconds = day['dailySleepDTO'].get('sleepTimeSeconds', 0)
                    sleep_hours = sleep_seconds / 3600
                    
                    sleep_data.append({
                        'date': day['dailySleepDTO']['calendarDate'],
                        'score': overall_score,
                        'duration': sleep_hours
                    })
            
            # Sort by date
            sleep_data.sort(key=lambda x: x['date'])
            return sleep_data
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error getting sleep data: {e}")
            return []


class DashboardGenerator:
    def __init__(self, strava_client, garmin_client):
        self.strava_client = strava_client
        self.garmin_client = garmin_client
        plt.style.use('dark_background')
        plt.rcParams.update({
            'text.color': TEXT_COLOR,
            'axes.labelcolor': TEXT_COLOR,
            'axes.edgecolor': TEXT_COLOR,
            'axes.facecolor': BACKGROUND_COLOR,
            'figure.facecolor': BACKGROUND_COLOR,
            'xtick.color': TEXT_COLOR,
            'ytick.color': TEXT_COLOR,
            'grid.color': '#30363d',
            'figure.figsize': (10, 6),
            'font.size': 12
        })

    def generate_weekly_chart(self):
        """Generate weekly running distance chart"""
        weekly_data = self.strava_client.get_weekly_stats()
        
        if not weekly_data:
            logger.warning("No weekly data available for chart")
            return None
            
        # Prepare data for plotting
        weeks = [parser.parse(data['week']) for data in weekly_data]
        distances = [data['distance'] for data in weekly_data]
        
        # Create the plot
        fig, ax = plt.subplots(figsize=(10, 5))
        
        # Plot the data
        ax.bar(weeks, distances, color=STRAVA_ORANGE, alpha=0.8, width=5)
        
        # Add trend line
        z = np.polyfit(range(len(distances)), distances, 1)
        p = np.poly1d(z)
        ax.plot(weeks, p(range(len(distances))), color=SECONDARY_COLOR, linestyle='--', linewidth=2)
        
        # Configure the plot
        ax.set_title('Weekly Running Distance', fontsize=16, pad=20)
        ax.set_xlabel('Week', fontsize=12)
        ax.set_ylabel('Distance (miles)', fontsize=12)
        
        # Format x-axis to show dates nicely
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %d'))
        ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
        plt.xticks(rotation=45)
        
        # Add grid for readability
        ax.grid(True, linestyle='--', alpha=0.3)
        
        # Ensure padding
        plt.tight_layout()
        
        # Save the chart to buffer
        img_path = IMAGES_DIR / "weekly_distance.svg"
        plt.savefig(img_path, format='svg', transparent=False)
        plt.close()
        
        return img_path

    def generate_sleep_chart(self):
        """Generate sleep score chart"""
        sleep_data = self.garmin_client.get_sleep_data()
        
        if not sleep_data:
            logger.warning("No sleep data available for chart")
            return None
            
        # Prepare data for plotting
        dates = [parser.parse(data['date']) for data in sleep_data]
        scores = [data['score'] for data in sleep_data]
        durations = [data['duration'] for data in sleep_data]
        
        # Create figure with two y-axes
        fig, ax1 = plt.subplots(figsize=(10, 5))
        ax2 = ax1.twinx()
        
        # Plot sleep score as line
        line1 = ax1.plot(dates, scores, 'o-', color=STRAVA_ORANGE, linewidth=2, label='Sleep Score')
        ax1.set_ylim(0, 100)
        ax1.set_ylabel('Sleep Score', color=STRAVA_ORANGE)
        ax1.tick_params(axis='y', colors=STRAVA_ORANGE)
        
        # Plot sleep duration as bars
        bars = ax2.bar(dates, durations, alpha=0.3, color=SECONDARY_COLOR, label='Sleep Duration')
        ax2.set_ylabel('Hours', color=SECONDARY_COLOR)
        ax2.tick_params(axis='y', colors=SECONDARY_COLOR)
        
        # Configure the plot
        ax1.set_title('Sleep Quality Trend', fontsize=16, pad=20)
        ax1.set_xlabel('Date', fontsize=12)
        
        # Format x-axis to show dates nicely
        ax1.xaxis.set_major_formatter(mdates.DateFormatter('%b %d'))
        ax1.xaxis.set_major_locator(mdates.DayLocator(interval=2))
        plt.xticks(rotation=45)
        
        # Add grid for readability
        ax1.grid(True, linestyle='--', alpha=0.3)
        
        # Add legend
        lines, labels = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(lines + lines2, labels + labels2, loc='upper left')
        
        # Ensure padding
        plt.tight_layout()
        
        # Save the chart to buffer
        img_path = IMAGES_DIR / "sleep_score.svg"
        plt.savefig(img_path, format='svg', transparent=False)
        plt.close()
        
        return img_path

    def generate_race_map(self, race):
        """Generate static map for latest race"""
        if not race or not race.get('map') or not race['map'].get('polyline'):
            logger.warning("No race data available for map generation")
            return None
            
        # Decode the polyline
        poly = polyline.decode(race['map']['polyline'])
        
        # Calculate bounds
        lats = [p[0] for p in poly]
        lons = [p[1] for p in poly]
        
        min_lat, max_lat = min(lats), max(lats)
        min_lon, max_lon = min(lons), max(lons)
        
        # Add some padding
        lat_padding = (max_lat - min_lat) * 0.1
        lon_padding = (max_lon - min_lon) * 0.1
        
        min_lat -= lat_padding
        max_lat += lat_padding
        min_lon -= lon_padding
        max_lon += lon_padding
        
        # Calculate width and height (in pixels) to maintain aspect ratio
        width = 800
        aspect_ratio = (max_lon - min_lon) / (max_lat - min_lat)
        height = int(width / aspect_ratio) if aspect_ratio > 1 else int(width * (1/aspect_ratio))
        
        # Create a static map
        m = StaticMap(width, height)
        
        # Create the track line
        line = Line(poly, STRAVA_ORANGE, 3)
        m.add_line(line)
        
        # Add start and finish markers
        start_marker = CircleMarker(poly[0], 'green', 10)
        finish_marker = CircleMarker(poly[-1], 'red', 10)
        m.add_marker(start_marker)
        m.add_marker(finish_marker)
        
        # Render the map
        image = m.render()
        
        # Save the map
        img_path = IMAGES_DIR / "race_map.svg"
        
        # Convert PIL Image to SVG (simplified approach)
        # This is a simplified approach. In a real implementation, you might want to use a proper
        # library for converting raster to SVG or use a SVG mapping library directly.
        svg_content = f'''
        <svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
            <image href="data:image/png;base64,{base64.b64encode(image.tobytes()).decode('utf-8')}" 
                   width="{width}" height="{height}" />
        </svg>
        '''
        
        with open(img_path, 'w') as f:
            f.write(svg_content)
        
        return img_path

    def update_readme(self):
        """Update the README file with all generated data"""
        try:
            # Get data
            activities = self.strava_client.get_activities(5)
            weekly_chart_path = self.generate_weekly_chart()
            sleep_chart_path = self.generate_sleep_chart()
            ytd_stats = self.strava_client.get_ytd_stats()
            personal_records = self.strava_client.get_personal_records()
            
            # Get latest race and generate map
            latest_race = self.strava_client.get_recent_race()
            race_map_path = None
            if latest_race:
                race_map_path = self.generate_race_map(latest_race)
            
            # Get sleep data
            sleep_data = self.garmin_client.get_sleep_data()
            
            # Read existing README
            with open('README.md', 'r') as f:
                readme_content = f.read()
            
            # Update weekly chart
            if weekly_chart_path:
                relative_path = weekly_chart_path.relative_to(Path.cwd())
                updated_content = re.sub(
                    r'(<!-- STRAVA_WEEKLY_CHART:START -->).*?(<!-- STRAVA_WEEKLY_CHART:END -->)',
                    f'\\1\n![My Weekly Running Distance](/{relative_path})\n\\2',
                    readme_content,
                    flags=re.DOTALL
                )
                readme_content = updated_content
            
            # Update personal records
            if personal_records:
                prs_md = "| Distance | Time | Pace | Date | Location |\n"
                prs_md += "|----------|------|------|------|----------|\n"
                
                for pr in personal_records:
                    prs_md += f"| {pr['distance']} | {pr['time']} | {pr['pace']} | {pr['date']} | {pr['location']} |\n"
                
                updated_content = re.sub(
                    r'(<!-- STRAVA_PRS:START -->).*?(<!-- STRAVA_PRS:END -->)',
                    f'\\1\n{prs_md}\\2',
                    readme_content,
                    flags=re.DOTALL
                )
                readme_content = updated_content
            
            # Update recent activities
            if activities:
                activities_md = "| Date | Activity | Distance | Time | Pace | Elevation |\n"
                activities_md += "|------|----------|----------|------|------|----------|\n"
                
                for activity in activities:
                    if activity['type'] == 'Run':
                        date = parser.parse(activity['start_date_local']).strftime('%Y-%m-%d')
                        distance = f"{activity['distance'] / 1609.34:.2f} mi"
                        time = self.strava_client._format_time(activity['moving_time'])
                        pace = self.strava_client._calculate_pace(activity['moving_time'], activity['distance'])
                        elevation = f"{activity.get('total_elevation_gain', 0) * 3.28084:.0f} ft"
                        
                        activities_md += f"| {date} | {activity['name']} | {distance} | {time} | {pace} | {elevation} |\n"
                
                updated_content = re.sub(
                    r'(<!-- STRAVA_ACTIVITIES:START -->).*?(<!-- STRAVA_ACTIVITIES:END -->)',
                    f'\\1\n{activities_md}\\2',
                    readme_content,
                    flags=re.DOTALL
                )
                readme_content = updated_content
            
            # Update latest race
            if latest_race and race_map_path:
                relative_path = race_map_path.relative_to(Path.cwd())
                race_date = parser.parse(latest_race['start_date_local']).strftime('%Y-%m-%d')
                race_distance = f"{latest_race['distance'] / 1609.34:.2f} mi"
                race_time = self.strava_client._format_time(latest_race['moving_time'])
                race_pace = self.strava_client._calculate_pace(latest_race['moving_time'], latest_race['distance'])
                
                race_md = f"![My Latest Race Map](/{relative_path})\n\n"
                race_md += f"**Race**: {latest_race['name']}  \n"
                race_md += f"**Date**: {race_date}  \n"
                race_md += f"**Distance**: {race_distance}  \n"
                race_md += f"**Time**: {race_time}  \n"
                race_md += f"**Pace**: {race_pace}  \n"
                
                if latest_race.get('achievement_count'):
                    race_md += f"**Achievements**: {latest_race['achievement_count']}  \n"
                
                updated_content = re.sub(
                    r'(<!-- STRAVA_RACE:START -->).*?(<!-- STRAVA_RACE:END -->)',
                    f'\\1\n{race_md}\\2',
                    readme_content,
                    flags=re.DOTALL
                )
                readme_content = updated_content
            
            # Update sleep stats
            if sleep_data and sleep_chart_path:
                relative_path = sleep_chart_path.relative_to(Path.cwd())
                
                # Calculate last night's sleep and weekly average
                last_night = sleep_data[-1] if sleep_data else None
                weekly_data = sleep_data[-7:] if len(sleep_data) >= 7 else sleep_data
                
                weekly_avg_duration = sum(day['duration'] for day in weekly_data) / len(weekly_data)
                weekly_avg_score = sum(day['score'] for day in weekly_data) / len(weekly_data)
                
                sleep_md = f"![My Sleep Score Trend](/{relative_path})\n\n"
                
                if last_night:
                    hours = int(last_night['duration'])
                    minutes = int((last_night['duration'] - hours) * 60)
                    sleep_md += f"**Last Night**: {hours}h {minutes}m (Sleep Score: {last_night['score']})  \n"
                
                hours_avg = int(weekly_avg_duration)
                minutes_avg = int((weekly_avg_duration - hours_avg) * 60)
                sleep_md += f"**Weekly Average**: {hours_avg}h {minutes_avg}m (Sleep Score: {weekly_avg_score:.0f})  \n"
                
                updated_content = re.sub(
                    r'(<!-- SLEEP_STATS:START -->).*?(<!-- SLEEP_STATS:END -->)',
                    f'\\1\n{sleep_md}\\2',
                    readme_content,
                    flags=re.DOTALL
                )
                readme_content = updated_content
            
            # Update YTD stats
            if ytd_stats:
                distance = f"{ytd_stats.get('distance', 0) / 1609.34:.1f} mi"
                elevation = f"{ytd_stats.get('elevation_gain', 0) * 3.28084:.0f} ft"
                
                hours = int(ytd_stats.get('moving_time', 0) / 3600)
                minutes = int((ytd_stats.get('moving_time', 0) % 3600) / 60)
                time = f"{hours}h {minutes}m"
                
                activities_count = ytd_stats.get('count', 0)
                
                ytd_md = f"- **Distance**: {distance}\n"
                ytd_md += f"- **Elevation**: {elevation}\n"
                ytd_md += f"- **Time**: {time}\n"
                ytd_md += f"- **Activities**: {activities_count}\n"
                
                updated_content = re.sub(
                    r'(<!-- STRAVA_YTD:START -->).*?(<!-- STRAVA_YTD:END -->)',
                    f'\\1\n{ytd_md}\\2',
                    readme_content,
                    flags=re.DOTALL
                )
                readme_content = updated_content
            
            # Update last updated timestamp
            now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')
            updated_content = re.sub(
                r'{{LAST_UPDATED}}',
                now,
                readme_content
            )
            readme_content = updated_content
            
            # Write the updated README
            with open('README.md', 'w') as f:
                f.write(readme_content)
                
            logger.info("README updated successfully")
            return True
            
        except Exception as e:
            logger.error(f"Error updating README: {e}")
            return False


def main():
    # Initialize clients
    strava_client = StravaClient()
    garmin_client = GarminClient()
    
    # Create dashboard generator
    dashboard = DashboardGenerator(strava_client, garmin_client)
    
    # Update README
    dashboard.update_readme()


if __name__ == "__main__":
    main()
