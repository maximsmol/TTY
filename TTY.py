import sublime, sublime_plugin
import os, threading, fcntl
import subprocess, pty, shlex, signal

ST2 = int(sublime.version()) < 3000

ESC = '\x1B'
CSI = ESC+'['

class Codes:
	recognized_but_unknown = -2
	unknown = -1
	cursor_save_position = 0
	cursor_restore_position = 1

	cursor_hide = 2
	cursor_show = 3

	cursor_move_up = 4
	cursor_move_down = 5
	cursor_move_forward = 6
	cursor_move_backward = 7

	cursor_move_next_line = 8
	cursor_move_previous_line = 9

	cursor_move_absolute_horizontal = 10
	cursor_move_absolute_both = 11

	erase_all = 12
	erase_line = 13

	scroll_up = 14
	scroll_down = 15

	cursor_move_absolute_both_alternative = 16
	set_graphics_options = 17

class SGROptions:
	reset = 0
	bold = 1
	faint = 2
	italic = 3
	underline = 4
	blink_slow = 5
	blink_rapid = 6
	negative = 7
	conceal = 8 # todo: wtf
	strike_out = 9

	font = 10
	font_default = 10
	font_alternative_1 = 11
	font_alternative_2 = 12
	font_alternative_3 = 13
	font_alternative_4 = 14
	font_alternative_5 = 15
	font_alternative_6 = 16
	font_alternative_7 = 17
	font_alternative_8 = 18
	font_alternative_9 = 19
	franktur = 20 # todo: wtf
	bold_off_or_underline_double = 21 # todo: which is standard?
	not_bold_or_faint = 22
	not_italic_or_franktur = 23
	no_underline = 24
	no_blink = 25
	reversed = 26
	positive = 27
	reveal = 28
	no_strike_out = 29
	color_foreground_black   = 30
	color_foreground_red     = 31
	color_foreground_green   = 32
	color_foreground_yellow  = 33
	color_foreground_blue    = 34
	color_foreground_magenta = 35
	color_foreground_cyan    = 36
	color_foreground_white   = 37
	color_foreground_extended = 38
	color_foreground_default = 39
	color_background_black   = 40
	color_background_red     = 41
	color_background_green   = 42
	color_background_yellow  = 43
	color_background_blue    = 44
	color_background_magenta = 45
	color_background_cyan    = 46
	color_background_white   = 47
	color_background_extended = 48
	color_background_default = 49
	reserved_50 = 50 # todo: wtf
	frame = 51
	encircle = 52
	overline = 53
	no_frame_or_encircle = 54
	no_overline = 55
	reserved_56 = 56
	reserved_57 = 57
	reserved_58 = 58
	reserved_59 = 59
	ideogram_underline_or_right_side_line = 60 # todo: wtf; which is standard?
	ideogram_underline_or_right_side_line_double = 61 # todo: wtf; which is standard?
	ideogram_overline_or_left_side_line = 62 # todo: wtf; which is standard?
	ideogram_overline_or_left_side_line_double = 63 # todo: wtf; which is standard?
	ideogram_stress_marking = 64 # todo: wtf
	ideogram_off = 65

class TtyHelperReplaceCommand(sublime_plugin.TextCommand):
	def run(self, edit, start, end, new_text):
		self.view.replace(edit, sublime.Region(start, end), new_text)

terminal_views = {}
def add_terminal_view(view, process, that):
	global terminal_views
	terminal_views[view.buffer_id()] = {'proc': process, 'cmd_instance': that}

def remove_terminal_view(view):
	global terminal_views
	del terminal_views[view.buffer_id()]

class TtyBecomeTerminalCommand(sublime_plugin.TextCommand):
	READ_BY = 100
	MAX_BUFFER_SIZE = 3000
	FLUSH_TIMEOUT = 200

	BINARY = True
	BINARY_ENCODING = 'ascii'
	def __init__(self, view):
		self.command = ''
		self.proc = None
		self.running = False

		self.master = None

		self.cursor_pos_row = 0
		self.cursor_pos_col = 0

		self.new_cursor_pos_row = 0
		self.new_cursor_pos_col = 0

		self.buffer = ''
		self.buffer_size = 0

		sublime_plugin.TextCommand.__init__(self, view)

	def replace(self, start, end, new):
		self.view.run_command('tty_helper_replace', {'start': start, 'end': end, 'new_text': new})

	def update_sel(self):
		pass
		# sel = self.view.sel()
		# sel.clear()
		# sel.add(sublime.Region(self.cursor_pos(), self.cursor_pos()))

	def cursor_pos(self):
		return self.view.text_point(self.cursor_pos_row, self.cursor_pos_col)

	def new_cursor_pos(self):
		return self.view.text_point(self.new_cursor_pos_row, self.new_cursor_pos_col)

	def set_cursor_pos(self, p):
		(self.cursor_pos_row, self.cursor_pos_col) = self.view.rowcol(p)
		self.update_sel()

	def move_to_new_pos(self):
		self.cursor_pos_row = self.new_cursor_pos_row
		self.cursor_pos_col = self.new_cursor_pos_col

		self.update_sel()

	def send_eof(self):
		self.send_chars('\x04')

	def send_chars(self, chars):
		if not self.running:
			sublime.message_dialog('Process finished. Sending data to child process failed')
			return

		if self.BINARY:
			chars = bytearray(chars, self.BINARY_ENCODING)
		self.master.write(chars)

	def send_escaped(self, chars):
		self.send_chars('\x1B'+chars)

	def flush(self):
		if self.buffer_size == 0:
			return

		self.replace(self.cursor_pos(), self.new_cursor_pos(), self.buffer)

		self.move_to_new_pos()

		self.buffer = ''
		self.buffer_size = 0

	def run_command(self, cmd):
		self.view.set_name(cmd)

		argv = shlex.split(cmd)
		(masterfd, slavefd) = pty.openpty()
		try:
			old_flags = fcntl.fcntl(masterfd, fcntl.F_GETFL)
			fcntl.fcntl(masterfd, fcntl.F_SETFL, old_flags|os.O_NONBLOCK)

			master = os.fdopen(masterfd, 'r+'+('b' if self.BINARY else ''), 0)

			self.master = master

			with subprocess.Popen(argv, stdin=slavefd, stdout=slavefd, stderr=slavefd) as proc:
				add_terminal_view(self.view, proc, self)

				self.proc = proc

				self.running = True
				while proc.poll() is None:
					while True:
						s = master.read(self.READ_BY)
						if not s:
							break
						if self.BINARY:
							s = s.decode(self.BINARY_ENCODING)

						self.buffer += s
						self.buffer_size += len(s)

						l = self.new_cursor_pos_col
						for c in s:
							if c == '\n':
								l = 0
								self.new_cursor_pos_row += 1
							elif c == '\r':
								l = 0
							else:
								l += 1

						self.new_cursor_pos_col = l

						if self.buffer_size > self.MAX_BUFFER_SIZE:
							self.flush()

					self.flush()

				self.running = False
				self.proc = None

				self.master = None
		finally:
			try:
				os.close(masterfd)
				os.close(slavefd)
			except OSError:
				pass

			self.view.set_name(cmd+' <finished>')


	def work(self):
		self.run_command(self.command)

	def run(self, _, command):
		self.command = command
		threading.Thread(target=self.work).start()

SIGNALS = ['SIGHUP', 'SIGINT', 'SIGQUIT', 'SIGILL', 'SIGTRAP', 'SIGABRT', 'SIGEMT', 'SIGFPE', 'SIGKILL', 'SIGBUS', 'SIGSEGV', 'SIGSYS', 'SIGPIPE', 'SIGALRM', 'SIGTERM', 'SIGUSR1', 'SIGUSR2', 'SIGCHLD', 'SIGWINCH', 'SIGURG', 'SIGSTOP', 'SIGTSTP', 'SIGCONT', 'SIGTTIN', 'SIGTTOU', 'SIGVTALRM', 'SIGPROF', 'SIGXCPU', 'SIGXFSZ']
SIGNAL_CODES = [signal.SIGHUP, signal.SIGINT, signal.SIGQUIT, signal.SIGILL, signal.SIGTRAP, signal.SIGABRT, signal.SIGEMT, signal.SIGFPE, signal.SIGKILL, signal.SIGBUS, signal.SIGSEGV, signal.SIGSYS, signal.SIGPIPE, signal.SIGALRM, signal.SIGTERM, signal.SIGUSR1, signal.SIGUSR2, signal.SIGCHLD, signal.SIGWINCH, signal.SIGURG, signal.SIGSTOP, signal.SIGTSTP, signal.SIGCONT, signal.SIGTTIN, signal.SIGTTOU, signal.SIGVTALRM, signal.SIGPROF, signal.SIGXCPU, signal.SIGXFSZ]
class TtyListCommand(sublime_plugin.ApplicationCommand):
	def __init__(self):
		self.window = None

		self.view_names = {}
		self.view_indices = {}

		self.chosen_view = None

		sublime_plugin.ApplicationCommand.__init__(self)

	def send_signal(self, i):
		if i == -1:
			return

		if self.chosen_view in terminal_views:
			proc = terminal_views[self.chosen_view]['proc']

			if proc.poll() is not None:
				sublime.message_dialog('Cannot send signal. Process finished')
				return

			proc.send_signal(SIGNAL_CODES[i])

	def open_action_panel(self, i):
		if i == -1:
			return

		self.chosen_view = self.view_indices[i]
		self.window.show_quick_panel(SIGNALS, self.send_signal)

	def run(self):
		self.window = sublime.active_window()
		view = self.window.active_view()
		my_id = view.buffer_id()

		self.view_names = {}
		for w in sublime.windows():
			for v in w.views():
				self.view_names[v.buffer_id()] = v.name()

		i = 0
		sel = -1
		self.view_indices = {}
		list = []
		for v in terminal_views:
			if v == my_id:
				sel = i
			self.view_indices[i] = v
			list.append(self.view_names[v]+'('+str(v)+') - pid:'+str(terminal_views[v]['proc'].pid))

			i += 1

		if not list:
			list = ['No processes running']

		self.window.show_quick_panel(list, self.open_action_panel, sublime.KEEP_OPEN_ON_FOCUS_LOST, sel if sel != -1 else 0)

class TtySendEofCommand(sublime_plugin.TextCommand):
	def run(self, _):
		if self.view.buffer_id() not in terminal_views:
			sublime.error_message('TTY plugin has a bug! Send this to the devs: "tty_send_eof executed in non-terminal view"')
			return

		terminal_views[self.view.buffer_id()]['cmd_instance'].send_eof()

class TtySendCharsCommand(sublime_plugin.TextCommand):
	def run(self, _, chars):
		if self.view.buffer_id() not in terminal_views:
			sublime.error_message('TTY plugin has a bug! Send this to the devs: "tty_send_chars executed in non-terminal view"')
			return

		terminal_views[self.view.buffer_id()]['cmd_instance'].send_chars(chars)

class TtySendEscapedCommand(sublime_plugin.TextCommand):
	def run(self, _, chars):
		if self.view.buffer_id() not in terminal_views:
			sublime.error_message('TTY plugin has a bug! Send this to the devs: "tty_send_escaped executed in non-terminal view"')
			return

		terminal_views[self.view.buffer_id()]['cmd_instance'].send_escaped(chars)

class TtySendSignalCommand(sublime_plugin.TextCommand):
	def run(self, _, signal_name):
		if self.view.buffer_id() not in terminal_views:
			sublime.error_message('View is not a terminal!')
			return

		try:
			i = SIGNALS.index(signal_name)
			proc = terminal_views[self.view.buffer_id()]['proc']

			if proc.poll() is not None:
				sublime.message_dialog('Cannot send signal. Process finished')
				return

			proc.send_signal(SIGNAL_CODES[i])
		except ValueError:
			sublime.error_message('No such signal: '+signal_name)
			return

class TtyOpenCommand(sublime_plugin.ApplicationCommand):
	def run(self):
		term = sublime.active_window().new_file()
		term.set_scratch(True)

		os.chdir('/Users/maximsmol')
		term.run_command('tty_become_terminal', {'command': 'bash'})

class TtyEventListener(sublime_plugin.EventListener):
	def on_close(self, view):
		if view.buffer_id() in terminal_views:
			proc = terminal_views[view.buffer_id()]['proc']
			if proc.poll() is None:
				print('[TTY] killed process running in terminal: '+view.name()+'('+str(view.buffer_id())+')'+' pid:'+str(proc.pid))
				proc.kill()
			remove_terminal_view(view)

	def on_query_context(self, view, key, _, __, ___):
		if key == 'tty_is_in_terminal' and view.buffer_id() in terminal_views:
			return True

		return None
