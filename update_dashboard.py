#!/usr/bin/env python3
"""
GitHub README Dashboard Updater for Strava
Updates the README with running stats from Strava
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
STRAVA_ORANGE = "#FC4C02"
BACKGROUND_COLOR = "#0D1117"  # GitHub dark mode background
TEXT_COLOR = "#E6EDF3"  # GitHub dark mode text
SECONDARY_COLOR = "#58a6ff"  # GitHub accent blue
IMAGES_DIR = Path.cwd() / "images"

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
            week_start = (start_date - datetime.timedelta(days=start_date.weekday())).strftime('%B %d, %Y')
            
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
                    'date': parser.parse(activity['start_date_local']).strftime('%B %d, %Y'),
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


class DashboardGenerator:
    def __init__(self, strava_client):
        self.strava_client = strava_client
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

    def generate_race_map(self, race):
        """Generate static map for latest race"""
        if not race or not race.get('map') or not race['map'].get('polyline'):
            logger.warning("No race data available for map generation")
            return None
            
        # Decode the polyline
        poly = polyline.decode(race['map']['polyline'], geojson=True)
        
        # Calculate width and height (in pixels) to maintain aspect ratio
        width = 800
        height = width
        
        # Create a static map
        m = StaticMap(width, height, url_template='https://c.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png')
        
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
        img_path = IMAGES_DIR / "race_map.png"

        image.save(img_path)
        
        return img_path

    def update_readme(self):
        """Update the README file with all generated data"""
        try:
            # Get data
            activities = self.strava_client.get_activities(5)
            weekly_chart_path = self.generate_weekly_chart()
            ytd_stats = self.strava_client.get_ytd_stats()
            personal_records = self.strava_client.get_personal_records()
            
            # Get latest race and generate map
            latest_race = self.strava_client.get_recent_race()
            race_map_path = None
            if latest_race:
                race_map_path = self.generate_race_map(latest_race)
            
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
                prs_md = "| Distance | Time | Pace | Date |\n"
                prs_md += "|----------|------|------|------|\n"
                
                for pr in personal_records:
                    prs_md += f"| {pr['distance']} | {pr['time']} | {pr['pace']} | {pr['date']} |\n"
                
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
                        date = parser.parse(activity['start_date_local']).strftime('%B %d, %Y')
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
                race_date = parser.parse(latest_race['start_date_local']).strftime('%B %d, %Y')
                race_distance = f"{latest_race['distance'] / 1609.34:.2f} mi"
                race_time = self.strava_client._format_time(latest_race['moving_time'])
                race_pace = self.strava_client._calculate_pace(latest_race['moving_time'], latest_race['distance'])
                
                race_md = f'<img align="left" width="100" height="100" src="/{relative_path}">\n\n'
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
            now = datetime.datetime.now().strftime('%B %d, %Y %H:%M:%S UTC')
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
    
    # Create dashboard generator
    dashboard = DashboardGenerator(strava_client)
    
    # Update README
    dashboard.update_readme()


if __name__ == "__main__":
    main()
