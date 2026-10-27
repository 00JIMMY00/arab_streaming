app_name = "arab_streaming"
app_title = "Arab-ERP Streaming"
app_publisher = "Arab-ERP company"
app_description = "Durable, selected ERPNext data synchronization between master and branch sites"
app_email = "jimmygamaljimmy@gmail.com"
app_license = "mit"
required_apps = ["erpnext"]

after_install = "arab_streaming.install.after_install"
after_migrate = "arab_streaming.install.after_migrate"

# No business document hooks, network calls or scheduled event work until the
# isolated transport/executor acceptance gates pass. An unset site role is inert.
