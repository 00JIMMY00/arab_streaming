app_name = "arab_streaming"
app_title = "Arab-ERP Streaming"
app_publisher = "Arab-ERP company"
app_description = "Durable, selected ERPNext data synchronization between master and branch sites"
app_email = "jimmygamaljimmy@gmail.com"
app_license = "mit"
required_apps = ["erpnext"]

after_install = "arab_streaming.install.after_install"
after_migrate = "arab_streaming.install.after_migrate"

auth_hooks = ["arab_streaming.auth.authenticate_request"]

# Hooks are inert unless this site's role AND arab_streaming_enabled are set.
doc_events = {
	"*": {
		"before_validate": "arab_streaming.streaming.outbox.fence",
		"before_rename": "arab_streaming.streaming.outbox.fence",
		"after_insert": "arab_streaming.streaming.outbox.capture",
		"on_update": "arab_streaming.streaming.outbox.capture",
		"on_submit": "arab_streaming.streaming.outbox.capture",
		"on_update_after_submit": "arab_streaming.streaming.outbox.capture",
		"on_cancel": "arab_streaming.streaming.outbox.capture",
		"on_trash": ["arab_streaming.streaming.outbox.fence", "arab_streaming.streaming.outbox.capture"],
		"after_rename": "arab_streaming.streaming.outbox.capture",
	}
}
scheduler_events = {
	"cron": {"* * * * *": ["arab_streaming.tasks.wakeup"]},
	"daily": ["arab_streaming.streaming.retention.cleanup"],
}

extend_bootinfo = "arab_streaming.boot.extend_bootinfo"

doc_events["File"] = {"on_update": "arab_streaming.streaming.files.changed"}

doc_events["*"]["before_naming"] = "arab_streaming.streaming.naming.before_naming"
doc_events["*"]["before_validate"] = [
	"arab_streaming.streaming.naming.allow_series",
	"arab_streaming.streaming.outbox.fence",
]
doc_events["Branch"] = {
	"before_rename": "arab_streaming.streaming.naming.branch_identity",
	"on_trash": "arab_streaming.streaming.naming.branch_identity",
}
