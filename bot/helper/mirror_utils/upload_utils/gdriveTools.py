from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.http import MediaFileUpload
import pickle
import os
import threading
from bot import LOGGER, parent_id, DOWNLOAD_DIR, IS_TEAM_DRIVE, INDEX_URL
from bot.helper.ext_utils.fs_utils import get_mime_type
from bot.helper.ext_utils.bot_utils import *

logging.getLogger('googleapiclient.discovery').setLevel(logging.ERROR)


class GoogleDriveHelper:

    def __init__(self, name=None, listener=None):
        self.__G_DRIVE_TOKEN_FILE = "token.pickle"
        # Check https://developers.google.com/drive/scopes for all available scopes
        self.__OAUTH_SCOPE = "https://www.googleapis.com/auth/drive"
        # Redirect URI for installed apps, can be left as is
        self.__REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"
        self.__G_DRIVE_DIR_MIME_TYPE = "application/vnd.google-apps.folder"
        self.__G_DRIVE_BASE_DOWNLOAD_URL = "https://drive.google.com/uc?id={}&export=download"
        self.__listener = listener
        self.__service = self.authorize()
        self.__uploaded_bytes = 0
        self.start_time = 0
        self.is_uploading = True
        self.is_cancelled = False
        self.name = name
        self.resource_lock = threading.Lock()

    def cancel(self):
        with self.resource_lock:
            self.is_cancelled = True
            self.is_uploading = False

    @property
    def uploaded_bytes(self):
        with self.resource_lock:
            return self.__uploaded_bytes

    def speed(self):
        """
        It calculates the average upload speed and returns it in bytes/seconds unit
        :return: Upload speed in bytes/second
        """
        try:
            with self.resource_lock:
                return self.__uploaded_bytes / (time.time() - self.start_time)
        except ZeroDivisionError:
            return 0

    def __upload_empty_file(self, path, file_name, mime_type, parent_id=None):
        media_body = MediaFileUpload(path,
                                     mimetype=mime_type,
                                     resumable=False)
        file_metadata = {
            'name': file_name,
            'description': 'mirror',
            'mimeType': mime_type,
        }
        if parent_id is not None:
            file_metadata['parents'] = [parent_id]
        return self.__service.files().create(supportsTeamDrives=True,
                                             body=file_metadata, media_body=media_body).execute()

    def __set_permission(self, drive_id):
        permissions = {
            'role': 'reader',
            'type': 'anyone',
            'value': None,
            'withLink': True
        }
        return self.__service.permissions().create(supportsTeamDrives=True, fileId=drive_id, body=permissions).execute()

    def upload_file(self, file_path, file_name, mime_type, parent_id):
        # File body description
        file_metadata = {
            'name': file_name,
            'description': 'mirror',
            'mimeType': mime_type,
        }
        if parent_id is not None:
            file_metadata['parents'] = [parent_id]

        if os.path.getsize(file_path) == 0:
            media_body = MediaFileUpload(file_path,
                                         mimetype=mime_type,
                                         resumable=False)
            response = self.__service.files().create(supportsTeamDrives=True,
                                                     body=file_metadata, media_body=media_body).execute()
            if not IS_TEAM_DRIVE:
                self.__set_permission(response['id'])
            drive_file = self.__service.files().get(fileId=response['id']).execute()
            download_url = self.__G_DRIVE_BASE_DOWNLOAD_URL.format(drive_file.get('id'))
            return download_url
        media_body = MediaFileUpload(file_path,
                                     mimetype=mime_type,
                                     resumable=True,
                                     chunksize=50*1024*1024)

        # Insert a file
        drive_file = self.__service.files().create(supportsTeamDrives=True,
                                                   body=file_metadata, media_body=media_body)
        response = None
        last_uploaded = 0
        while response is None:
            if self.is_cancelled:
                return None
            status, response = drive_file.next_chunk()
            if status is not None:
                with self.resource_lock:
                    chunk_size = status.total_size * status.progress() - last_uploaded
                    last_uploaded = status.total_size * status.progress()
                    self.__uploaded_bytes += chunk_size
        # Insert new permissions
        if not IS_TEAM_DRIVE:
            self.__set_permission(response['id'])
        # Define file instance and get url for download
        drive_file = self.__service.files().get(supportsTeamDrives=True, fileId=response['id']).execute()
        download_url = self.__G_DRIVE_BASE_DOWNLOAD_URL.format(drive_file.get('id'))
        return download_url

    def upload(self, file_name: str):
        self.__listener.onUploadStarted()
        file_dir = f"{DOWNLOAD_DIR}{self.__listener.message.message_id}"
        file_path = f"{file_dir}/{file_name}"
        LOGGER.info("Uploading File: " + file_path)
        self.start_time = time.time()
        if os.path.isfile(file_path):
            try:
                mime_type = get_mime_type(file_path)
                link = self.upload_file(file_path, file_name, mime_type, parent_id)
                if link is None:
                    raise Exception('Upload has been manually cancelled')
                LOGGER.info("Uploaded To G-Drive: " + file_path)
            except Exception as e:
                LOGGER.error(str(e))
                e_str = str(e).replace('<', '')
                e_str = e_str.replace('>', '')
                self.__listener.onUploadError(e_str)
                return
        else:
            try:
                dir_id = self.create_directory(os.path.basename(os.path.abspath(file_name)), parent_id)
                result = self.upload_dir(file_path, dir_id)
                if result is None:
                    raise Exception('Upload has been manually cancelled!')
                LOGGER.info("Uploaded To G-Drive: " + file_name)
                link = f"https://drive.google.com/folderview?id={dir_id}"
            except Exception as e:
                LOGGER.error(str(e))
                e_str = str(e).replace('<', '')
                e_str = e_str.replace('>', '')
                self.__listener.onUploadError(e_str)
                return
        LOGGER.info(download_dict)
        self.__listener.onUploadComplete(link)
        LOGGER.info("Deleting downloaded file/folder..")
        return link

    def create_directory(self, directory_name, parent_id):
        file_metadata = {
            "name": directory_name,
            "mimeType": self.__G_DRIVE_DIR_MIME_TYPE
        }
        if parent_id is not None:
            file_metadata["parents"] = [parent_id]
        file = self.__service.files().create(supportsTeamDrives=True, body=file_metadata).execute()
        file_id = file.get("id")
        if not IS_TEAM_DRIVE:
            self.__set_permission(file_id)
        LOGGER.info("Created Google-Drive Folder:\nName: {}\nID: {} ".format(file.get("name"), file_id))
        return file_id

    def upload_dir(self, input_directory, parent_id):
        list_dirs = os.listdir(input_directory)
        if len(list_dirs) == 0:
            return parent_id
        new_id = None
        for item in list_dirs:
            current_file_name = os.path.join(input_directory, item)
            if self.is_cancelled:
                return None
            if os.path.isdir(current_file_name):
                current_dir_id = self.create_directory(item, parent_id)
                new_id = self.upload_dir(current_file_name, current_dir_id)
            else:
                mime_type = get_mime_type(current_file_name)
                file_name = current_file_name.split("/")[-1]
                # current_file_name will have the full path
                self.upload_file(current_file_name, file_name, mime_type, parent_id)
                new_id = parent_id
        return new_id

    def authorize(self):
        # Get credentials
        credentials = None
        if os.path.exists(self.__G_DRIVE_TOKEN_FILE):
            with open(self.__G_DRIVE_TOKEN_FILE, 'rb') as f:
                credentials = pickle.load(f)
        if credentials is None or not credentials.valid:
            if credentials and credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    'credentials.json', self.__OAUTH_SCOPE)
                LOGGER.info(flow)
                credentials = flow.run_console(port=0)

            # Save the credentials for the next run
            with open(self.__G_DRIVE_TOKEN_FILE, 'wb') as token:
                pickle.dump(credentials, token)
        return build('drive', 'v3', credentials=credentials, cache_discovery=False)

    def drive_list(self, fileName):
        msg = ""
        # Create Search Query for API request.
        query = f"'{parent_id}' in parents and (name contains '{fileName}')"
        page_token = None
        results = []
        while True:
            response = self.__service.files().list(supportsTeamDrives=True,
                                                   includeTeamDriveItems=True,
                                                   q=query,
                                                   spaces='drive',
                                                   fields='nextPageToken, files(id, name, mimeType, size)',
                                                   pageToken=page_token,
                                                   orderBy='modifiedTime desc').execute()
            for file in response.get('files', []):
                if len(results) >= 20:
                    break
                if file.get(
                        'mimeType') == "application/vnd.google-apps.folder":  # Detect Whether Current Entity is a Folder or File.
                    msg += f"⁍ <a href='https://drive.google.com/drive/folders/{file.get('id')}'>{file.get('name')}" \
                           f"</a> (folder)"
                    if INDEX_URL is not None:
                        url = f'{INDEX_URL}/{file.get("name")}/'
                        msg += f' | <a href="{url}"> Index URL</a>'
                else:
                    msg += f"⁍ <a href='https://drive.google.com/uc?id={file.get('id')}" \
                           f"&export=download'>{file.get('name')}</a> ({get_readable_file_size(int(file.get('size')))})"
                    if INDEX_URL is not None:
                        url = f'{INDEX_URL}/{file.get("name")}'
                        msg += f' | <a href="{url}"> Index URL</a>'
                msg += '\n'
                results.append(file)
            page_token = response.get('nextPageToken', None)
            if page_token is None:
                break
        del results
        return msg
