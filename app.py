#!/usr/bin/env python3
"""
Streamlit App for Blinkit HOT Automation Workflows
Combines Gmail attachment downloader and Excel GRN processor with real-time tracking
"""

import streamlit as st
import os
import json
import base64
import tempfile
import time
import logging
import pandas as pd
import zipfile
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from io import StringIO
import threading
import queue
import re
import io
import warnings
from lxml import etree

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow, Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
import zipfile

warnings.filterwarnings("ignore")

# Configure Streamlit page
st.set_page_config(
    page_title="Blinkit HOT Automation",
    page_icon="🔥",
    layout="wide",
    initial_sidebar_state="expanded"
)

class BlinkitHOTAutomation:
    def __init__(self):
        self.gmail_service = None
        self.drive_service = None
        self.sheets_service = None
        
        # API scopes
        self.gmail_scopes = ['https://www.googleapis.com/auth/gmail.readonly']
        self.drive_scopes = ['https://www.googleapis.com/auth/drive']
        self.sheets_scopes = ['https://www.googleapis.com/auth/spreadsheets']
    
    def authenticate_from_secrets(self, progress_bar, status_text):
        """Authenticate using Streamlit secrets with web-based OAuth flow"""
        try:
            status_text.text("Authenticating with Google APIs...")
            progress_bar.progress(10)
            
            # Check for existing token in session state
            if 'oauth_token' in st.session_state:
                try:
                    combined_scopes = list(set(self.gmail_scopes + self.drive_scopes + self.sheets_scopes))
                    creds = Credentials.from_authorized_user_info(st.session_state.oauth_token, combined_scopes)
                    if creds and creds.valid:
                        progress_bar.progress(50)
                        # Build services
                        self.gmail_service = build('gmail', 'v1', credentials=creds)
                        self.drive_service = build('drive', 'v3', credentials=creds)
                        self.sheets_service = build('sheets', 'v4', credentials=creds)
                        progress_bar.progress(100)
                        status_text.text("Authentication successful!")
                        return True
                    elif creds and creds.expired and creds.refresh_token:
                        creds.refresh(Request())
                        st.session_state.oauth_token = json.loads(creds.to_json())
                        # Build services
                        self.gmail_service = build('gmail', 'v1', credentials=creds)
                        self.drive_service = build('drive', 'v3', credentials=creds)
                        self.sheets_service = build('sheets', 'v4', credentials=creds)
                        progress_bar.progress(100)
                        status_text.text("Authentication successful!")
                        return True
                except Exception as e:
                    st.info(f"Cached token invalid, requesting new authentication: {str(e)}")
            
            # Use Streamlit secrets for OAuth
            if "google" in st.secrets and "credentials_json" in st.secrets["google"]:
                creds_data = json.loads(st.secrets["google"]["credentials_json"])
                combined_scopes = list(set(self.gmail_scopes + self.drive_scopes + self.sheets_scopes))
                
                # Configure for web application
                flow = Flow.from_client_config(
                    client_config=creds_data,
                    scopes=combined_scopes,
                    redirect_uri="https://blinkit-hot-grn.streamlit.app/"  # Update with your actual URL
                )
                
                # Generate authorization URL
                auth_url, _ = flow.authorization_url(prompt='consent')
                
                # Check for callback code
                query_params = st.query_params
                if "code" in query_params:
                    try:
                        code = query_params["code"]
                        flow.fetch_token(code=code)
                        creds = flow.credentials
                        
                        # Save credentials in session state
                        st.session_state.oauth_token = json.loads(creds.to_json())
                        
                        progress_bar.progress(50)
                        # Build services
                        self.gmail_service = build('gmail', 'v1', credentials=creds)
                        self.drive_service = build('drive', 'v3', credentials=creds)
                        self.sheets_service = build('sheets', 'v4', credentials=creds)
                        
                        progress_bar.progress(100)
                        status_text.text("Authentication successful!")
                        
                        # Clear the code from URL
                        st.query_params.clear()
                        return True
                    except Exception as e:
                        st.error(f"Authentication failed: {str(e)}")
                        return False
                else:
                    # Show authorization link
                    st.markdown("### Google Authentication Required")
                    st.markdown(f"[Authorize with Google]({auth_url})")
                    st.info("Click the link above to authorize, you'll be redirected back automatically")
                    st.stop()
            else:
                st.error("Google credentials missing in Streamlit secrets")
                return False
                
        except Exception as e:
            st.error(f"Authentication failed: {str(e)}")
            return False
    
    def search_emails(self, sender: str = "", search_term: str = "", 
                     days_back: int = 7, max_results: int = 50) -> List[Dict]:
        """Search for emails with attachments"""
        try:
            # Build search query
            query_parts = ["has:attachment"]
            
            if sender:
                query_parts.append(f'from:"{sender}"')
            
            if search_term:
                if "," in search_term:
                    keywords = [k.strip() for k in search_term.split(",")]
                    keyword_query = " OR ".join([f'"{k}"' for k in keywords if k])
                    if keyword_query:
                        query_parts.append(f"({keyword_query})")
                else:
                    query_parts.append(f'"{search_term}"')
            
            # Add date filter
            start_date = datetime.now() - timedelta(days=days_back)
            query_parts.append(f"after:{start_date.strftime('%Y/%m/%d')}")
            
            query = " ".join(query_parts)
            st.info(f"Searching Gmail with query: {query}")
            
            # Execute search
            result = self.gmail_service.users().messages().list(
                userId='me', q=query, maxResults=max_results
            ).execute()
            
            messages = result.get('messages', [])
            st.info(f"Gmail search returned {len(messages)} messages")
            
            return messages
            
        except Exception as e:
            st.error(f"Email search failed: {str(e)}")
            return []
    
    def process_gmail_workflow(self, config: dict, progress_bar, status_text):
        """Process Gmail attachment download workflow"""
        try:
            status_text.text("Starting Gmail workflow...")
            
            # Search for emails
            emails = self.search_emails(
                sender=config['sender'],
                search_term=config['search_term'],
                days_back=config['days_back'],
                max_results=config['max_results']
            )
            
            progress_bar.progress(25)
            
            if not emails:
                st.warning("No emails found matching criteria")
                return {'success': True, 'processed': 0}
            
            status_text.text(f"Found {len(emails)} emails. Processing attachments...")
            st.info(f"Found {len(emails)} emails matching criteria")
            
            # Create base folder in Drive
            base_folder_name = "Gmail_Attachments"
            base_folder_id = self._create_drive_folder(base_folder_name, config.get('gdrive_folder_id'))
            
            if not base_folder_id:
                st.error("Failed to create base folder in Google Drive")
                return {'success': False, 'processed': 0}
            
            progress_bar.progress(50)
            
            processed_count = 0
            total_attachments = 0
            
            for i, email in enumerate(emails):
                try:
                    status_text.text(f"Processing email {i+1}/{len(emails)}")
                    
                    # Get email details first
                    email_details = self._get_email_details(email['id'])
                    subject = email_details.get('subject', 'No Subject')[:50]
                    sender = email_details.get('sender', 'Unknown')
                    
                    st.info(f"Processing email: {subject} from {sender}")
                    
                    # Get full message with payload
                    message = self.gmail_service.users().messages().get(
                        userId='me', id=email['id'], format='full'
                    ).execute()
                    
                    if not message or not message.get('payload'):
                        st.warning(f"No payload found for email: {subject}")
                        continue
                    
                    # Extract attachments
                    attachment_count = self._extract_attachments_from_email(
                        email['id'], message['payload'], sender, config, base_folder_id
                    )
                    
                    total_attachments += attachment_count
                    if attachment_count > 0:
                        processed_count += 1
                        st.success(f"Found {attachment_count} attachments in: {subject}")
                    else:
                        st.info(f"No matching attachments in: {subject}")
                    
                    progress = 50 + (i + 1) / len(emails) * 45
                    progress_bar.progress(int(progress))
                    
                except Exception as e:
                    st.error(f"Failed to process email {email.get('id', 'unknown')}: {str(e)}")
            
            progress_bar.progress(100)
            status_text.text(f"Gmail workflow completed! Processed {total_attachments} attachments from {processed_count} emails")
            
            return {'success': True, 'processed': total_attachments}
            
        except Exception as e:
            st.error(f"Gmail workflow failed: {str(e)}")
            return {'success': False, 'processed': 0}
    
    def process_excel_workflow(self, config: dict, progress_bar, status_text):
        """Process Excel GRN workflow"""
        try:
            status_text.text("Starting Excel GRN workflow...")
            
            # Get Excel files with 'GRN' in name from Drive folder
            excel_files = self._get_excel_files_with_grn(config['excel_folder_id'])
            
            progress_bar.progress(25)
            
            if not excel_files:
                st.warning("No Excel files with 'GRN' found in the specified folder")
                return {'success': True, 'processed': 0}
            
            status_text.text(f"Found {len(excel_files)} GRN files. Processing...")
            st.info(f"Found {len(excel_files)} Excel files containing 'GRN'")
            
            processed_count = 0
            is_first_file = True
            
            for i, file in enumerate(excel_files):
                try:
                    status_text.text(f"Processing Excel file {i+1}/{len(excel_files)}: {file['name']}")
                    
                    # Read Excel file
                    df = self._read_excel_file(file['id'], file['name'], config['header_row'])
                    
                    if df.empty:
                        st.warning(f"No data extracted from: {file['name']}")
                        continue
                    
                    st.info(f"Data shape: {df.shape} - Columns: {list(df.columns)[:3]}{'...' if len(df.columns) > 3 else ''}")
                    
                    # Append to Google Sheet
                    self._append_to_sheet(
                        config['spreadsheet_id'], 
                        config['sheet_name'], 
                        df, 
                        is_first_file
                    )
                    
                    st.success(f"Appended data from: {file['name']}")
                    processed_count += 1
                    is_first_file = False
                    
                    progress = 25 + (i + 1) / len(excel_files) * 70
                    progress_bar.progress(int(progress))
                    
                except Exception as e:
                    st.error(f"Failed to process Excel file {file.get('name', 'unknown')}: {str(e)}")
            
            # Remove duplicates
            if processed_count > 0:
                status_text.text("Removing duplicates from Google Sheet...")
                self._remove_duplicates_from_sheet(
                    config['spreadsheet_id'], 
                    config['sheet_name']
                )
                st.info("Removed duplicates from Google Sheet")
            
            progress_bar.progress(100)
            status_text.text(f"Excel workflow completed! Processed {processed_count} files")
            
            return {'success': True, 'processed': processed_count}
            
        except Exception as e:
            st.error(f"Excel workflow failed: {str(e)}")
            return {'success': False, 'processed': 0}
    
    def _get_email_details(self, message_id: str) -> Dict:
        """Get email details including sender and subject"""
        try:
            message = self.gmail_service.users().messages().get(
                userId='me', id=message_id, format='metadata'
            ).execute()
            
            headers = message['payload'].get('headers', [])
            
            details = {
                'id': message_id,
                'sender': next((h['value'] for h in headers if h['name'] == "From"), "Unknown"),
                'subject': next((h['value'] for h in headers if h['name'] == "Subject"), "(No Subject)"),
                'date': next((h['value'] for h in headers if h['name'] == "Date"), "")
            }
            
            return details
            
        except Exception as e:
            st.error(f"Failed to get email details for {message_id}: {str(e)}")
            return {'id': message_id, 'sender': 'Unknown', 'subject': 'Unknown', 'date': ''}
    
    def _create_drive_folder(self, folder_name: str, parent_folder_id: Optional[str] = None) -> str:
        """Create a folder in Google Drive"""
        try:
            # Check if folder already exists
            query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
            if parent_folder_id:
                query += f" and '{parent_folder_id}' in parents"
            
            existing = self.drive_service.files().list(q=query, fields='files(id, name)').execute()
            files = existing.get('files', [])
            
            if files:
                return files[0]['id']
            
            # Create new folder
            folder_metadata = {
                'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder'
            }
            
            if parent_folder_id:
                folder_metadata['parents'] = [parent_folder_id]
            
            folder = self.drive_service.files().create(
                body=folder_metadata,
                fields='id'
            ).execute()
            
            return folder.get('id')
            
        except Exception as e:
            st.error(f"Failed to create folder {folder_name}: {str(e)}")
            return ""
    
    def _sanitize_filename(self, filename: str) -> str:
        """Clean up filenames to be safe for all operating systems"""
        cleaned = re.sub(r'[<>:"/\\|?*]', '_', filename)
        if len(cleaned) > 100:
            name_parts = cleaned.split('.')
            if len(name_parts) > 1:
                extension = name_parts[-1]
                base_name = '.'.join(name_parts[:-1])
                cleaned = f"{base_name[:95]}.{extension}"
            else:
                cleaned = cleaned[:100]
        return cleaned
    
    def _file_exists_in_folder(self, filename: str, folder_id: str) -> bool:
        """Check if file already exists in folder"""
        try:
            query = f"name='{filename}' and '{folder_id}' in parents and trashed=false"
            existing = self.drive_service.files().list(q=query, fields='files(id, name)').execute()
            files = existing.get('files', [])
            return len(files) > 0
        except Exception as e:
            st.error(f"Failed to check file existence: {str(e)}")
            return False
    
    def _extract_attachments_from_email(self, message_id: str, payload: Dict, sender: str, config: dict, base_folder_id: str) -> int:
        """Extract attachments from email with proper folder structure"""
        processed_count = 0
        
        if "parts" in payload:
            for part in payload["parts"]:
                processed_count += self._extract_attachments_from_email(
                    message_id, part, sender, config, base_folder_id
                )
        elif payload.get("filename") and "attachmentId" in payload.get("body", {}):
            filename = payload.get("filename", "")
            
            # Filter for Excel files only
            if not filename.lower().endswith(('.xls', '.xlsx', '.xlsm')):
                return 0
            
            try:
                # Get attachment data
                attachment_id = payload["body"].get("attachmentId")
                att = self.gmail_service.users().messages().attachments().get(
                    userId='me', messageId=message_id, id=attachment_id
                ).execute()
                
                file_data = base64.urlsafe_b64decode(att["data"].encode("UTF-8"))
                
                # Create nested folder structure: Gmail_Attachments -> sender -> search_term -> file_type
                sender_email = sender
                if "<" in sender_email and ">" in sender_email:
                    sender_email = sender_email.split("<")[1].split(">")[0].strip()
                sender_folder_name = self._sanitize_filename(sender_email)
                search_term = config.get('search_term', 'all-attachments')
                search_folder_name = search_term if search_term else "all-attachments"
                file_type_folder = "Excel_Files"
                
                # Create sender folder
                sender_folder_id = self._create_drive_folder(sender_folder_name, base_folder_id)
                
                # Create search term folder
                search_folder_id = self._create_drive_folder(search_folder_name, sender_folder_id)
                
                # Create file type folder within search folder
                type_folder_id = self._create_drive_folder(file_type_folder, search_folder_id)
                
                # Clean filename and make it unique
                clean_filename = self._sanitize_filename(filename)
                final_filename = f"{message_id}_{clean_filename}"
                
                # Check if file already exists
                if not self._file_exists_in_folder(final_filename, type_folder_id):
                    # Upload to Drive
                    file_metadata = {
                        'name': final_filename,
                        'parents': [type_folder_id]
                    }
                    
                    media = MediaIoBaseUpload(
                        io.BytesIO(file_data),
                        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        resumable=True
                    )
                    
                    self.drive_service.files().create(
                        body=file_metadata,
                        media_body=media,
                        fields='id'
                    ).execute()
                    
                    st.info(f"Uploaded: {final_filename}")
                    processed_count = 1
                else:
                    st.info(f"File already exists, skipping: {final_filename}")
                
            except Exception as e:
                st.error(f"Failed to process attachment {filename}: {str(e)}")
        
        return processed_count
    
    def _get_excel_files_with_grn(self, folder_id: str) -> List[Dict]:
        """Get Excel files with 'GRN' in the name from Google Drive folder"""
        try:
            # Query for Excel files containing 'GRN' in the name
            query = f"'{folder_id}' in parents and (mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' or mimeType='application/vnd.ms-excel') and name contains 'GRN' and trashed=false"
            
            files = []
            page_token = None
            
            while True:
                request_params = {
                    'q': query,
                    'fields': "files(id, name), nextPageToken",
                    'pageSize': 100
                }
                
                if page_token:
                    request_params['pageToken'] = page_token
                
                results = self.drive_service.files().list(**request_params).execute()
                
                batch_files = results.get('files', [])
                files.extend(batch_files)
                
                page_token = results.get('nextPageToken')
                if not page_token:
                    break
            
            st.info(f"Found {len(files)} GRN Excel files")
            return files
            
        except Exception as e:
            st.error(f"Failed to list Excel files: {str(e)}")
            return []
    
    def _read_excel_file(self, file_id: str, filename: str, header_row: int) -> pd.DataFrame:
        """Read Excel file from Google Drive with multiple fallback strategies"""
        try:
            # Download file
            request = self.drive_service.files().get_media(fileId=file_id)
            file_stream = io.BytesIO()
            downloader = MediaIoBaseDownload(file_stream, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
            file_stream.seek(0)
            
            st.info(f"Reading Excel file: {filename} (Size: {len(file_stream.getvalue())} bytes)")
            
            # Strategy 1: Try openpyxl engine first
            try:
                file_stream.seek(0)
                if header_row == -1:
                    df = pd.read_excel(file_stream, engine="openpyxl", header=None)
                else:
                    df = pd.read_excel(file_stream, engine="openpyxl", header=header_row)
                if not df.empty:
                    df = self._clean_dataframe(df)
                    return df
            except Exception as e:
                st.warning(f"openpyxl failed: {str(e)[:50]}...")
            
            # Strategy 2: Try xlrd for .xls files
            if filename.lower().endswith('.xls'):
                try:
                    file_stream.seek(0)
                    if header_row == -1:
                        df = pd.read_excel(file_stream, engine="xlrd", header=None)
                    else:
                        df = pd.read_excel(file_stream, engine="xlrd", header=header_row)
                    if not df.empty:
                        df = self._clean_dataframe(df)
                        return df
                except Exception as e:
                    st.warning(f"xlrd failed: {str(e)[:50]}...")
            
            # Strategy 3: Raw XML extraction for corrupted files
            df = self._try_raw_xml_extraction(file_stream, header_row)
            if not df.empty:
                df = self._clean_dataframe(df)
                return df
            
            st.error(f"All strategies failed for: {filename}")
            return pd.DataFrame()
            
        except Exception as e:
            st.error(f"Failed to read Excel file {filename}: {str(e)}")
            return pd.DataFrame()
    
    def _try_raw_xml_extraction(self, file_stream, header_row):
        """Raw XML extraction with proper text handling"""
        try:
            file_stream.seek(0)
            with zipfile.ZipFile(file_stream, 'r') as zip_ref:
                # Look for shared strings
                shared_strings = {}
                shared_strings_file = 'xl/sharedStrings.xml'
                if shared_strings_file in zip_ref.namelist():
                    try:
                        with zip_ref.open(shared_strings_file) as ss_file:
                            ss_content = ss_file.read().decode('utf-8', errors='ignore')
                            string_pattern = r'<t[^>]*>([^<]*)</t>'
                            strings = re.findall(string_pattern, ss_content, re.DOTALL)
                            for i, string_val in enumerate(strings):
                                shared_strings[str(i)] = string_val.strip()
                    except Exception:
                        pass
                
                # Look for worksheet files
                worksheet_files = [f for f in zip_ref.namelist() if 'xl/worksheets/' in f and f.endswith('.xml')]
                if not worksheet_files:
                    return pd.DataFrame()
                
                # Try to read the first worksheet
                with zip_ref.open(worksheet_files[0]) as xml_file:
                    content = xml_file.read().decode('utf-8', errors='ignore')
                    
                    # Extract cell references with their types and values
                    cell_pattern = r'<c[^>]*r="([A-Z]+\d+)"[^>]*(?:t="([^"]*)")?[^>]*>(?:.*?<v[^>]*>([^<]*)</v>)?(?:.*?<is><t[^>]*>([^<]*)</t></is>)?'
                    cells = re.findall(cell_pattern, content, re.DOTALL)
                    
                    if not cells:
                        return pd.DataFrame()
                    
                    # Convert cell references to row/col coordinates
                    cell_data = {}
                    max_row = 0
                    max_col = 0
                    
                    for cell_ref, cell_type, v_value, is_value in cells:
                        # Parse cell reference (e.g., "A1" -> row 1, col 1)
                        col_letters = ''.join([c for c in cell_ref if c.isalpha()])
                        row_num = int(''.join([c for c in cell_ref if c.isdigit()]))
                        
                        col_num = 0
                        for c in col_letters:
                            col_num = col_num * 26 + (ord(c) - ord('A') + 1)
                        
                        # Determine the actual cell value based on type
                        if is_value:  # Inline string
                            cell_value = is_value.strip()
                        elif cell_type == 's' and v_value:  # Shared string reference
                            cell_value = shared_strings.get(v_value, v_value)
                        elif cell_type == 'str' and v_value:  # String
                            cell_value = v_value.strip()
                        elif v_value:  # Number or other value
                            cell_value = v_value.strip()
                        else:
                            cell_value = ""
                        
                        cell_data[(row_num, col_num)] = self._clean_cell_value(cell_value)
                        max_row = max(max_row, row_num)
                        max_col = max(max_col, col_num)
                    
                    if not cell_data:
                        return pd.DataFrame()
                    
                    # Convert to 2D array
                    data = []
                    for row in range(1, max_row + 1):
                        row_data = []
                        for col in range(1, max_col + 1):
                            row_data.append(cell_data.get((row, col), ""))
                        if any(cell for cell in row_data):  # Only add non-empty rows
                            data.append(row_data)
                    
                    if len(data) < max(1, header_row + 2):
                        return pd.DataFrame()
                    
                    if header_row == -1:
                        # No headers - create generic column names
                        headers = [f"Column_{i+1}" for i in range(len(data[0]))]
                        return pd.DataFrame(data, columns=headers)
                    else:
                        # Use specified header row
                        if len(data) > header_row:
                            headers = [str(h) if h else f"Column_{i+1}" for i, h in enumerate(data[header_row])]
                            return pd.DataFrame(data[header_row+1:], columns=headers)
                        else:
                            return pd.DataFrame()
                    
        except Exception as e:
            st.warning(f"Raw XML extraction failed: {str(e)[:50]}...")
            return pd.DataFrame()
    
    def _clean_cell_value(self, value):
        """Clean and standardize cell values"""
        if value is None:
            return ""
        if isinstance(value, (int, float)):
            if pd.isna(value):
                return ""
            return str(value)
        # Convert to string and remove single quotes
        cleaned = str(value).strip().replace("'", "")
        return cleaned
    
    def _clean_dataframe(self, df):
        """Clean DataFrame by removing rows with blank B column, duplicates, and single quotes"""
        if df.empty:
            return df
        
        st.info(f"Original DataFrame shape: {df.shape}")
        
        # Remove single quotes from all string columns
        string_columns = df.select_dtypes(include=['object']).columns
        for col in string_columns:
            df[col] = df[col].astype(str).str.replace("'", "", regex=False)
        
        # Remove rows where second column (B column) is blank/empty
        if len(df.columns) >= 2:
            second_col = df.columns[1]
            mask = ~(
                df[second_col].isna() | 
                (df[second_col].astype(str).str.strip() == "") |
                (df[second_col].astype(str).str.strip() == "nan")
            )
            df = df[mask]
            st.info(f"After removing blank B column rows: {df.shape}")
        
        # Remove duplicate rows
        original_count = len(df)
        df = df.drop_duplicates()
        duplicates_removed = original_count - len(df)
        
        if duplicates_removed > 0:
            st.info(f"Removed {duplicates_removed} duplicate rows")
        
        st.info(f"Final cleaned DataFrame shape: {df.shape}")
        return df
    
    def _append_to_sheet(self, spreadsheet_id: str, sheet_name: str, df: pd.DataFrame, is_first_file: bool):
        """Append DataFrame to Google Sheet"""
        try:
            # Convert DataFrame to values
            if is_first_file:
                # Include headers for first file
                values = [df.columns.tolist()] + df.fillna('').astype(str).values.tolist()
            else:
                # Skip headers for subsequent files
                values = df.fillna('').astype(str).values.tolist()
            
            if not values:
                st.warning("No data to append")
                return
            
            # Prepare the request body
            body = {
                'values': values
            }
            
            # Append data to the sheet
            result = self.sheets_service.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id,
                range=f"{sheet_name}!A:A",
                valueInputOption='USER_ENTERED',
                insertDataOption='INSERT_ROWS',
                body=body
            ).execute()
            
            st.info(f"Appended {len(values)} rows to Google Sheet")
            
        except Exception as e:
            st.error(f"Failed to append to Google Sheet: {str(e)}")
    
    def _remove_duplicates_from_sheet(self, spreadsheet_id: str, sheet_name: str):
        """Remove duplicate rows from Google Sheet"""
        try:
            # Get all data from the sheet
            result = self.sheets_service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range=f"{sheet_name}!A:ZZ"
            ).execute()
            
            values = result.get('values', [])
            
            if len(values) <= 1:
                st.info("No data rows to check for duplicates")
                return
            
            # Keep track of seen rows (excluding header)
            headers = values[0] if values else []
            unique_rows = [headers]  # Start with headers
            seen_rows = set()
            duplicates_count = 0
            
            for row in values[1:]:  # Skip header row
                # Pad row to match header length
                padded_row = row + [''] * (len(headers) - len(row))
                row_tuple = tuple(padded_row)
                
                if row_tuple not in seen_rows:
                    seen_rows.add(row_tuple)
                    unique_rows.append(padded_row)
                else:
                    duplicates_count += 1
            
            if duplicates_count > 0:
                # Clear the sheet and write unique data back
                self.sheets_service.spreadsheets().values().clear(
                    spreadsheetId=spreadsheet_id,
                    range=f"{sheet_name}!A:ZZ"
                ).execute()
                
                # Write unique data back
                body = {'values': unique_rows}
                self.sheets_service.spreadsheets().values().update(
                    spreadsheetId=spreadsheet_id,
                    range=f"{sheet_name}!A1",
                    valueInputOption='USER_ENTERED',
                    body=body
                ).execute()
                
                st.info(f"Removed {duplicates_count} duplicate rows from Google Sheet")
            else:
                st.info("No duplicate rows found")
                
        except Exception as e:
            st.error(f"Failed to remove duplicates: {str(e)}")


def create_streamlit_ui():
    """Create the Streamlit user interface"""
    st.title("🔥 Blinkit HOT Automation")
    st.markdown("### Automated Gmail Attachment Processing & Excel GRN Consolidation")
    
    # Initialize automation object
    if 'automation' not in st.session_state:
        st.session_state.automation = BlinkitHOTAutomation()
    
    # Sidebar for navigation
    st.sidebar.title("Navigation")
    workflow_choice = st.sidebar.selectbox(
        "Select Workflow",
        ["Gmail Attachment Downloader", "Excel GRN Processor", "Combined Workflow"]
    )
    
    # Authentication section
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 🔐 Authentication")
    
    if st.sidebar.button("Authenticate Google APIs", key="auth_button"):
        with st.spinner("Authenticating..."):
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            success = st.session_state.automation.authenticate_from_secrets(progress_bar, status_text)
            
            if success:
                st.sidebar.success("✅ Authentication successful!")
                st.session_state.authenticated = True
            else:
                st.sidebar.error("❌ Authentication failed")
                st.session_state.authenticated = False
    
    # Check if authenticated
    if not st.session_state.get('authenticated', False):
        st.warning("⚠️ Please authenticate with Google APIs first using the sidebar")
        st.stop()
    
    st.sidebar.success("✅ Authenticated")
    
    # Main content based on workflow choice
    if workflow_choice == "Gmail Attachment Downloader":
        create_gmail_workflow_ui()
    elif workflow_choice == "Excel GRN Processor":
        create_excel_workflow_ui()
    else:
        create_combined_workflow_ui()


def create_gmail_workflow_ui():
    """Create Gmail workflow UI"""
    st.header("📧 Gmail Attachment Downloader")
    st.markdown("Download Excel attachments from Gmail and organize them in Google Drive")
    
    with st.expander("Configuration", expanded=True):
        col1, col2 = st.columns(2)
        
        with col1:
            sender = st.text_input(
                "Sender Email (optional)",
                placeholder="sender@example.com",
                help="Filter emails from specific sender"
            )
            
            search_term = st.text_input(
                "Search Terms",
                placeholder="GRN, invoice, report",
                help="Comma-separated keywords to search for"
            )
        
        with col2:
            days_back = st.number_input(
                "Days Back to Search",
                min_value=1,
                max_value=365,
                value=7,
                help="How many days back to search emails"
            )
            
            max_results = st.number_input(
                "Maximum Results",
                min_value=1,
                max_value=500,
                value=50,
                help="Maximum number of emails to process"
            )
        
        gdrive_folder_id = st.text_input(
            "Google Drive Folder ID (optional)",
            placeholder="1ABC_defGHI23jklMNO456pqrSTU789vwxYZ",
            help="Parent folder ID in Google Drive. Leave empty for root folder."
        )
    
    if st.button("🚀 Start Gmail Workflow", type="primary"):
        config = {
            'sender': sender.strip(),
            'search_term': search_term.strip(),
            'days_back': days_back,
            'max_results': max_results,
            'gdrive_folder_id': gdrive_folder_id.strip() if gdrive_folder_id.strip() else None
        }
        
        with st.spinner("Processing Gmail workflow..."):
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            result = st.session_state.automation.process_gmail_workflow(config, progress_bar, status_text)
            
            if result['success']:
                st.balloons()
                st.success(f"🎉 Gmail workflow completed! Processed {result['processed']} attachments")
            else:
                st.error("❌ Gmail workflow failed")


def create_excel_workflow_ui():
    """Create Excel workflow UI"""
    st.header("📊 Excel GRN Processor")
    st.markdown("Process Excel files with 'GRN' in their names and consolidate data")
    
    with st.expander("Configuration", expanded=True):
        col1, col2 = st.columns(2)
        
        with col1:
            excel_folder_id = st.text_input(
                "Excel Files Folder ID *",
                placeholder="1ABC_defGHI23jklMNO456pqrSTU789vwxYZ",
                help="Google Drive folder ID containing Excel files with 'GRN' in names"
            )
            
            spreadsheet_id = st.text_input(
                "Target Google Sheet ID *",
                placeholder="1ABC_defGHI23jklMNO456pqrSTU789vwxYZ",
                help="Google Sheets ID where data will be consolidated"
            )
        
        with col2:
            sheet_name = st.text_input(
                "Sheet Name *",
                value="Consolidated_GRN_Data",
                help="Name of the sheet tab to write data to"
            )
            
            header_row = st.number_input(
                "Header Row Number",
                min_value=-1,
                value=0,
                help="Row number containing headers (0-based). Use -1 for no headers."
            )
    
    if st.button("🚀 Start Excel Workflow", type="primary"):
        if not excel_folder_id.strip() or not spreadsheet_id.strip() or not sheet_name.strip():
            st.error("❌ Please fill in all required fields marked with *")
            return
        
        config = {
            'excel_folder_id': excel_folder_id.strip(),
            'spreadsheet_id': spreadsheet_id.strip(),
            'sheet_name': sheet_name.strip(),
            'header_row': header_row
        }
        
        with st.spinner("Processing Excel workflow..."):
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            result = st.session_state.automation.process_excel_workflow(config, progress_bar, status_text)
            
            if result['success']:
                st.balloons()
                st.success(f"🎉 Excel workflow completed! Processed {result['processed']} files")
            else:
                st.error("❌ Excel workflow failed")


def create_combined_workflow_ui():
    """Create combined workflow UI"""
    st.header("🔄 Combined Workflow")
    st.markdown("Run both Gmail attachment download and Excel GRN processing in sequence")
    
    # Gmail Configuration
    with st.expander("📧 Gmail Configuration", expanded=True):
        col1, col2 = st.columns(2)
        
        with col1:
            gmail_sender = st.text_input(
                "Sender Email (optional)",
                placeholder="sender@example.com",
                help="Filter emails from specific sender",
                key="combined_gmail_sender"
            )
            
            gmail_search_term = st.text_input(
                "Search Terms",
                placeholder="GRN, invoice, report",
                help="Comma-separated keywords to search for",
                key="combined_gmail_search"
            )
        
        with col2:
            gmail_days_back = st.number_input(
                "Days Back to Search",
                min_value=1,
                max_value=365,
                value=7,
                help="How many days back to search emails",
                key="combined_gmail_days"
            )
            
            gmail_max_results = st.number_input(
                "Maximum Results",
                min_value=1,
                max_value=500,
                value=50,
                help="Maximum number of emails to process",
                key="combined_gmail_max"
            )
        
        gmail_folder_id = st.text_input(
            "Google Drive Folder ID (optional)",
            placeholder="1ABC_defGHI23jklMNO456pqrSTU789vwxYZ",
            help="Parent folder ID in Google Drive. Leave empty for root folder.",
            key="combined_gmail_folder"
        )
    
    # Excel Configuration
    with st.expander("📊 Excel Configuration", expanded=True):
        col1, col2 = st.columns(2)
        
        with col1:
            excel_folder_id = st.text_input(
                "Excel Files Folder ID *",
                placeholder="1ABC_defGHI23jklMNO456pqrSTU789vwxYZ",
                help="Google Drive folder ID containing Excel files with 'GRN' in names",
                key="combined_excel_folder"
            )
            
            spreadsheet_id = st.text_input(
                "Target Google Sheet ID *",
                placeholder="1ABC_defGHI23jklMNO456pqrSTU789vwxYZ",
                help="Google Sheets ID where data will be consolidated",
                key="combined_spreadsheet"
            )
        
        with col2:
            sheet_name = st.text_input(
                "Sheet Name *",
                value="Consolidated_GRN_Data",
                help="Name of the sheet tab to write data to",
                key="combined_sheet_name"
            )
            
            header_row = st.number_input(
                "Header Row Number",
                min_value=-1,
                value=0,
                help="Row number containing headers (0-based). Use -1 for no headers.",
                key="combined_header_row"
            )
    
    # Workflow Options
    with st.expander("⚙️ Workflow Options", expanded=False):
        skip_gmail = st.checkbox(
            "Skip Gmail workflow",
            help="Skip the Gmail attachment download and only process existing Excel files"
        )
        
        skip_excel = st.checkbox(
            "Skip Excel workflow", 
            help="Only download Gmail attachments without processing Excel files"
        )
    
    if st.button("🚀 Start Combined Workflow", type="primary"):
        if not skip_excel and (not excel_folder_id.strip() or not spreadsheet_id.strip() or not sheet_name.strip()):
            st.error("❌ Please fill in all required Excel fields marked with * or check 'Skip Excel workflow'")
            return
        
        gmail_config = {
            'sender': gmail_sender.strip(),
            'search_term': gmail_search_term.strip(),
            'days_back': gmail_days_back,
            'max_results': gmail_max_results,
            'gdrive_folder_id': gmail_folder_id.strip() if gmail_folder_id.strip() else None
        }
        
        excel_config = {
            'excel_folder_id': excel_folder_id.strip(),
            'spreadsheet_id': spreadsheet_id.strip(),
            'sheet_name': sheet_name.strip(),
            'header_row': header_row
        }
        
        with st.spinner("Processing combined workflow..."):
            overall_progress = st.progress(0)
            status_text = st.empty()
            
            gmail_success = True
            excel_success = True
            gmail_processed = 0
            excel_processed = 0
            
            # Run Gmail workflow
            if not skip_gmail:
                status_text.text("Starting Gmail workflow...")
                gmail_progress = st.progress(0)
                gmail_status = st.empty()
                
                gmail_result = st.session_state.automation.process_gmail_workflow(
                    gmail_config, gmail_progress, gmail_status
                )
                gmail_success = gmail_result['success']
                gmail_processed = gmail_result['processed']
                
                overall_progress.progress(50)
                
                if gmail_success:
                    st.success(f"✅ Gmail workflow completed! Processed {gmail_processed} attachments")
                else:
                    st.error("❌ Gmail workflow failed")
            else:
                overall_progress.progress(50)
                st.info("⏭️ Skipping Gmail workflow")
            
            # Run Excel workflow
            if not skip_excel and (gmail_success or skip_gmail):
                status_text.text("Starting Excel workflow...")
                excel_progress = st.progress(0)
                excel_status = st.empty()
                
                excel_result = st.session_state.automation.process_excel_workflow(
                    excel_config, excel_progress, excel_status
                )
                excel_success = excel_result['success']
                excel_processed = excel_result['processed']
                
                if excel_success:
                    st.success(f"✅ Excel workflow completed! Processed {excel_processed} files")
                else:
                    st.error("❌ Excel workflow failed")
            elif skip_excel:
                st.info("⏭️ Skipping Excel workflow")
            
            overall_progress.progress(100)
            status_text.text("Combined workflow completed!")
            
            # Final summary
            if (gmail_success or skip_gmail) and (excel_success or skip_excel):
                st.balloons()
                summary = f"🎉 Combined workflow completed successfully!"
                if not skip_gmail:
                    summary += f"\n📧 Gmail: {gmail_processed} attachments processed"
                if not skip_excel:
                    summary += f"\n📊 Excel: {excel_processed} files processed"
                st.success(summary)
            else:
                st.error("❌ Combined workflow completed with errors")


# Help and Information Section
def create_help_section():
    """Create help section with instructions"""
    with st.sidebar.expander("📋 Instructions", expanded=False):
        st.markdown("""
        ### Setup Steps:
        1. **Authenticate** with Google APIs using the button above
        2. **Choose workflow** from the dropdown
        3. **Configure settings** for your selected workflow
        4. **Run the workflow** using the start button
        
        ### Google Drive Folder ID:
        - Open the folder in Google Drive
        - Copy the ID from the URL after `/folders/`
        - Example: `1ABC_defGHI23jklMNO456pqrSTU789vwxYZ`
        
        ### Google Sheet ID:
        - Open the spreadsheet in Google Sheets
        - Copy the ID from the URL between `/d/` and `/edit`
        - Example: `1ABC_defGHI23jklMNO456pqrSTU789vwxYZ`
        """)
    
    with st.sidebar.expander("ℹ️ About", expanded=False):
        st.markdown("""
        **Blinkit HOT Automation v2.0**
        
        This application automates:
        - Gmail attachment downloading
        - Excel file processing and consolidation
        - Google Drive organization
        - Data deduplication
        
        Built with Streamlit and Google APIs.
        """)


def main():
    """Main function to run the Streamlit app"""
    try:
        # Initialize session state
        if 'authenticated' not in st.session_state:
            st.session_state.authenticated = False
        
        # Create UI
        create_streamlit_ui()
        create_help_section()
        
    except Exception as e:
        st.error(f"Application error: {str(e)}")
        st.info("Please refresh the page and try again.")


if __name__ == "__main__":
    main()