#-*- coding: utf-8 -*-

"""This file is for getting and setting up database connections
"""

KAPOWARR_DATABASE_FILE = "Kapowarr.db"

import logging
from os import makedirs
from os.path import dirname
from sqlite3 import Connection, Row
from threading import current_thread
from time import time

from flask import g

__DATABASE_VERSION__ = 3

class Singleton(type):
	_instances = {}
	def __call__(cls, *args, **kwargs):
		i = f'{cls}{current_thread()}'
		if i not in cls._instances:
			cls._instances[i] = super(Singleton, cls).__call__(*args, **kwargs)

		return cls._instances[i]

class DBConnection(Connection, metaclass=Singleton):
	"For creating a connection with a database"	
	file = ''
	
	def __init__(self, timeout: float) -> None:
		"""Create a connection with a database

		Args:
			timeout (float): How long to wait before giving up on a command
		"""
		logging.debug(f'Creating a connection to a database: {self.file}')
		super().__init__(self.file, timeout=timeout)
		super().cursor().execute("PRAGMA foreign_keys = ON;")
		return
	
def set_db_location(db_file_location: str) -> None:
	"""Setup database location. Create folder for database and set location for db.DBConnection

	Args:
		db_file_location (str): The absolute path to the database file
	"""
	# Create folder where file will be put in if it doesn't exist yet
	logging.debug(f'Setting database location: {db_file_location}')
	makedirs(dirname(db_file_location), exist_ok=True)
	DBConnection.file = db_file_location
	return

def get_db(output_type='tuple'):
	"""Get a database cursor instance or create a new one if needed

	Args:
		output_type ('tuple'|'dict', optional): The type of output the cursor should have. Defaults to 'tuple'.

	Returns:
		Cursor: Database cursor instance with desired output type set
	"""
	try:
		cursor = g.cursor
	except AttributeError:
		db = DBConnection(timeout=20.0)
		cursor = g.cursor = db.cursor()
		
	if output_type == 'dict':
		cursor.row_factory = Row
	else:
		cursor.row_factory = None

	return cursor

def close_db(e: str=None):
	"""Close database cursor, commit database and close database (setup after each request)

	Args:
		e (str, optional): Error. Defaults to None.
	"""

	try:
		cursor = g.cursor
		db = cursor.connection
		cursor.close()
		delattr(g, 'cursor')
		db.commit()
	except AttributeError:
		pass

	return

def migrate_db(current_db_version: int) -> None:
	"""
	Migrate a Kapowarr database from it's current version
	to the newest version supported by the Kapowarr version installed.
	"""
	logging.info('Migrating database to newer version...')
	cursor = get_db()
	if current_db_version == 1:
		# V1 -> V2
		cursor.executescript("DELETE FROM download_queue;")
		
		current_db_version = 2

	if current_db_version == 2:
		# V2 -> V3
		cursor.executescript("""
			BEGIN TRANSACTION;
			PRAGMA defer_foreign_keys = ON;
			
			-- Issues
			CREATE TEMPORARY TABLE temp_issues AS
				SELECT * FROM issues;
			DROP TABLE issues;

			CREATE TABLE issues(
				id INTEGER PRIMARY KEY,
				volume_id INTEGER NOT NULL,
				comicvine_id INTEGER NOT NULL,
				issue_number VARCHAR(20) NOT NULL,
				calculated_issue_number FLOAT(20) NOT NULL,
				title VARCHAR(255),
				date VARCHAR(10),
				description TEXT,
				monitored BOOL NOT NULL DEFAULT 1,

				FOREIGN KEY (volume_id) REFERENCES volumes(id)
					ON DELETE CASCADE
			);
			INSERT INTO issues
				SELECT * FROM temp_issues;

			-- Issues files				
			CREATE TEMPORARY TABLE temp_issues_files AS
				SELECT * FROM issues_files;
			DROP TABLE issues_files;
			
			CREATE TABLE issues_files(
				file_id INTEGER NOT NULL,
				issue_id INTEGER NOT NULL,

				FOREIGN KEY (file_id) REFERENCES files(id)
					ON DELETE CASCADE,
				FOREIGN KEY (issue_id) REFERENCES issues(id),
				CONSTRAINT PK_issues_files PRIMARY KEY (
					file_id,
					issue_id
				)
			);
			INSERT INTO issues_files
				SELECT * FROM temp_issues_files;

			COMMIT;
		""")
		
		current_db_version = 3
	
	return

def setup_db() -> None:
	"""Setup the database tables and default config when they aren't setup yet
	"""
	from backend.settings import (Settings, blocklist_reasons, default_settings,
	                              task_intervals)

	cursor = get_db()

	setup_commands = """
		CREATE TABLE IF NOT EXISTS config(
			key VARCHAR(100) PRIMARY KEY,
			value BLOB
		);
		CREATE TABLE IF NOT EXISTS root_folders(
			id INTEGER PRIMARY KEY,
			folder VARCHAR(254) UNIQUE NOT NULL
		);
		CREATE TABLE IF NOT EXISTS volumes(
			id INTEGER PRIMARY KEY,
			comicvine_id INTEGER NOT NULL,
			title VARCHAR(255) NOT NULL,
			year INTEGER(5),
			publisher VARCHAR(255),
			volume_number INTEGER(8) DEFAULT 1,
			description TEXT,
			cover BLOB,
			monitored BOOL NOT NULL DEFAULT 0,
			root_folder INTEGER NOT NULL,
			folder TEXT,
			
			FOREIGN KEY (root_folder) REFERENCES root_folders(id)
		);
		CREATE TABLE IF NOT EXISTS issues(
			id INTEGER PRIMARY KEY,
			volume_id INTEGER NOT NULL,
			comicvine_id INTEGER NOT NULL,
			issue_number VARCHAR(20) NOT NULL,
			calculated_issue_number FLOAT(20) NOT NULL,
			title VARCHAR(255),
			date VARCHAR(10),
			description TEXT,
			monitored BOOL NOT NULL DEFAULT 1,

			FOREIGN KEY (volume_id) REFERENCES volumes(id)
				ON DELETE CASCADE
		);
		CREATE INDEX IF NOT EXISTS issues_volume_number_index
			ON issues(volume_id, calculated_issue_number);
		CREATE TABLE IF NOT EXISTS files(
			id INTEGER PRIMARY KEY,
			filepath TEXT UNIQUE NOT NULL,
			size INTEGER
		);
		CREATE TABLE IF NOT EXISTS issues_files(
			file_id INTEGER NOT NULL,
			issue_id INTEGER NOT NULL,

			FOREIGN KEY (file_id) REFERENCES files(id)
				ON DELETE CASCADE,
			FOREIGN KEY (issue_id) REFERENCES issues(id),
			CONSTRAINT PK_issues_files PRIMARY KEY (
				file_id,
				issue_id
			)
		);
		CREATE TABLE IF NOT EXISTS download_queue(
			id INTEGER PRIMARY KEY,
			link TEXT NOT NULL,
			volume_id INTEGER NOT NULL,
			issue_id INTEGER,
			
			FOREIGN KEY (volume_id) REFERENCES volumes(id),
			FOREIGN KEY (issue_id) REFERENCES issues(id)
		);
		CREATE TABLE IF NOT EXISTS download_history(
			original_link TEXT NOT NULL,
			title TEXT NOT NULL,
			downloaded_at INTEGER NOT NULL
		);
		CREATE TABLE IF NOT EXISTS task_history(
			task_name NOT NULL,
			display_title NOT NULL,
			run_at INTEGER NOT NULL
		);
		CREATE TABLE IF NOT EXISTS task_intervals(
			task_name PRIMARY KEY,
			interval INTEGER NOT NULL,
			next_run INTEGER NOT NULL
		);
		CREATE TABLE IF NOT EXISTS blocklist_reasons(
			id INTEGER PRIMARY KEY,
			reason TEXT NOT NULL UNIQUE
		);
		CREATE TABLE IF NOT EXISTS blocklist(
			id INTEGER PRIMARY KEY,
			link TEXT NOT NULL UNIQUE,
			reason INTEGER NOT NULL,
			added_at INTEGER NOT NULL,

			FOREIGN KEY (reason) REFERENCES blocklist_reasons(id)
		);
	"""
	logging.debug('Creating database tables')
	cursor.executescript(setup_commands)

	# Insert default setting values
	logging.debug(f'Inserting default settings: {default_settings}')
	cursor.executemany(
		"""
		INSERT OR IGNORE INTO config
		VALUES (?,?);
		""",
		default_settings.items()
	)

	# Migrate database if needed
	current_db_version = int(cursor.execute(
		"SELECT value FROM config WHERE key = 'database_version' LIMIT 1;"
	).fetchone()[0])
	logging.debug(f'Database migration: {current_db_version} -> {__DATABASE_VERSION__}')
	if current_db_version < __DATABASE_VERSION__:
		migrate_db(current_db_version)
		cursor.execute(
			"UPDATE config SET value = ? WHERE key = 'database_version' LIMIT 1;",
			(__DATABASE_VERSION__,)
		)

	# Generate api key
	api_key = cursor.execute(
		"SELECT value FROM config WHERE key = 'api_key' LIMIT 1;"
	).fetchone()
	if api_key is None:
		cursor.execute("INSERT INTO config VALUES ('api_key', '');")
		Settings().generate_api_key()

	# Add task intervals
	current_time = round(time())
	logging.debug(f'Inserting task intervals: {task_intervals}')
	cursor.executemany(
		"""
		INSERT OR IGNORE INTO task_intervals
		VALUES (?,?,?);
		""",
		((k, v, current_time) for k, v in task_intervals.items())
	)
	
	# Add blocklist reasons
	logging.debug(f'Inserting blocklist reasons: {blocklist_reasons}')
	cursor.executemany(
		"""
		INSERT OR IGNORE INTO blocklist_reasons(id, reason)
		VALUES (?,?);
		""",
		blocklist_reasons.items()
	)

	return
