frappe.pages["customer-import-tool"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({parent: wrapper, title: __("Customer Import Tool"), single_column: true});
	const method = "arab_streaming.customer_import.";
	const escape = frappe.utils.escape_html;
	let fileUrl = null, digest = null, busy = false, recheckTimer = null;
	const storageKey = "customer-import-job:" + frappe.session.user;
	const body = $(page.body).append(`<div class="customer-import" style="max-width:1100px;margin:24px auto;padding:0 16px">
		<div class="frappe-card p-4 mb-4">
			<h3>${__("Bulk import customers")}</h3>
			<p class="text-muted">${__("Download the template, fill it in, upload it, then click Import Customers.")}</p>
			<a class="btn btn-default mb-3" href="/assets/arab_streaming/templates/customer-import-template.xlsx" download>${__("Download Excel Template")}</a>
			<div class="row defaults"></div>
			<details class="mt-3 mb-3"><summary>${__("Required fields and Excel instructions")}</summary><div class="alert alert-info mt-3">
				<strong>${__("Required:")}</strong> ${__("Customer Name and Customer Type. Customer Type must be Company, Individual or Partnership.")}<br>
				<strong>${__("Optional:")}</strong> ${__("Phone 1–3, Customer Group, and both address blocks. Each distinct phone creates a linked Contact; the first is primary.")}<br>
				<strong>${__("When adding an address:")}</strong> ${__("Address Line 1 and City are required. Use the Country selected above. Address 1 / Address 2 are optional short titles (maximum 140 characters); put the full street address in Address Line 1. Apartment, building and state are optional.")}<br>
				${__("Blank Customer Group uses the default above. Territory applies to all rows. Fill defaults required by your site's setup. Apartment and building are also preserved in Address Line 2.")}
			</div>
			<p class="text-muted">${__("Keep all 18 column headings unchanged. Format phone, apartment and building cells as Text to preserve leading zeros and + prefixes. Use values, not formulas. Existing customers with the same name are skipped without changes. Duplicate names within the workbook must be corrected.")}</p></details>
			<button class="btn btn-primary upload">${__("Upload Excel")}</button>
			<button class="btn btn-success import ml-2" disabled>${__("Import Customers")}</button>
			<p class="file-status text-muted mt-3" role="status"></p>
		</div>
		<div class="results" aria-live="polite"></div>
	</div>`).find(".customer-import");
	const controls = {};
	const invalidate = () => {
		digest = null;
		body.find(".import").prop("disabled", true);
		body.find(".results").empty();
		clearTimeout(recheckTimer);
		if (fileUrl && !busy) recheckTimer = setTimeout(() => run(0), 400);
	};
	for (const [fieldname, label, options] of [["customer_group", "Default Customer Group", "Customer Group"], ["territory", "Territory", "Territory"], ["country", "Country for Addresses", "Country"]]) {
		const column = $('<div class="col-md-4"></div>').appendTo(body.find(".defaults"));
		controls[fieldname] = frappe.ui.form.make_control({parent: column, df: {fieldname, label: __(label), fieldtype: "Link", options, onchange: invalidate}, render_input: true});
	}
	const setBusy = value => {
		busy = value;
		body.find(".upload").prop("disabled", value);
		body.find(".defaults input").prop("disabled", value);
		body.find(".import").prop("disabled", value || !digest);
	};
	async function run(apply, resumed = null) {
		if (busy || !fileUrl) return;
		const args = {file_url: fileUrl, apply, digest};
		for (const key of Object.keys(controls)) args[key] = controls[key].get_value() || null;
		setBusy(true);
		body.find(".file-status").text(apply ? __("Importing customers in the background…") : __("Validating workbook in the background…"));
		try {
			const started = resumed ? {message: resumed} : await frappe.call({method: method + "start_process", args});
			if (started.message.resumed) {
				apply = Number(started.message.apply);
				fileUrl = started.message.file_url;
				for (const [key, value] of Object.entries(started.message.defaults || {})) if (controls[key]) await controls[key].set_value(value || "");
			}
			sessionStorage.setItem(storageKey, JSON.stringify({job_id: started.message.job_id, apply, file_url: fileUrl, defaults: Object.fromEntries(Object.entries(controls).map(([key, control]) => [key, control.get_value() || ""]))}));
			const deadline = Date.now() + 3 * 60 * 60 * 1000;
			let response;
			while (true) {
				const status = await frappe.call({method: method + "job_status", args: {job_id: started.message.job_id}});
				if (!status.message) throw new Error(__("The server did not return a job status."));
				if (status.message.status === "finished") {
					sessionStorage.removeItem(storageKey);
					if (!status.message.result) throw new Error(__("The job result expired. Upload the file again."));
					if (status.message.result.error) throw new Error(status.message.result.error);
					response = {message: status.message.result};
					break;
				}
				if (["failed", "stopped", "canceled"].includes(status.message.status)) throw new Error(__("The background job stopped. Upload again to validate before retrying."));
				if (Date.now() > deadline) throw new Error(__("Status checking timed out. Reload this page to reconnect to the import."));
				const progress = status.message.progress;
				if (progress && progress.total) {
					const label = progress.phase === "importing" ? __("Creating customers") : __("Validating rows");
					body.find(".file-status").text(`${label}: ${progress.completed} / ${progress.total}` + (progress.phase === "importing" ? " · " + __("Customers become visible when the entire import completes.") : ""));
				} else if (status.message.status === "queued") {
					body.find(".file-status").text(__("Waiting for a background worker…"));
				}
				await new Promise(resolve => setTimeout(resolve, 1500));
			}
			sessionStorage.removeItem(storageKey);
				const result = response.message;
				const errors = result.rows.filter(r => r.status === "Error").length;
				const ready = result.rows.filter(r => r.status === "Ready").length;
				const skipped = result.rows.filter(r => r.status === "Skipped").length;
				digest = !apply && !errors && ready ? result.digest : null;
				const summary = apply ? `${result.imported} imported · ${skipped} skipped` : `${ready} ready · ${skipped} skipped · ${errors} errors`;
				body.find(".file-status").text(__("Processing complete."));
				body.find(".results").html(`<div class="frappe-card p-4"><h4>${escape(summary)}</h4><p class="text-muted">${apply ? __("Import complete.") : (errors ? __("Correct the errors in Excel, then upload again.") : (ready ? __("Ready. Click Import Customers to finish.") : __("All customers already exist. Nothing to import.")))}</p><div class="table-responsive"><table class="table table-bordered"><thead><tr><th>${__("Excel Row")}</th><th>${__("Customer")}</th><th>${__("Status")}</th><th>${__("Details")}</th></tr></thead><tbody>${result.rows.map(row => `<tr><td>${row.row}</td><td>${row.name ? `<a href="/app/customer/${encodeURIComponent(row.name)}">${escape(row.customer)}</a>` : escape(row.customer)}</td><td class="${row.status === 'Error' ? 'text-danger' : ''}">${escape(row.status)}</td><td>${escape(row.message)}</td></tr>`).join("")}</tbody></table></div></div>`);
		} catch (error) {
			digest = null;
			body.find(".file-status").text(error.message || __("Could not complete. Upload again to retry."));
			if (error.message) frappe.msgprint({title: __("Customer import"), message: escape(error.message), indicator: "red"});
		} finally {
			setBusy(false);
		}
	}
	body.find(".upload").on("click", () => new frappe.ui.FileUploader({
		restrictions: {allowed_file_types: [".xlsx"], max_file_size: 5 * 1024 * 1024},
		allow_multiple: false, make_attachments_public: false,
		on_success(file) {
			fileUrl = file.file_url;
			digest = null;
			body.find(".results").empty();
			body.find(".file-status").text(file.file_name);
			setBusy(false);
			run(0);
		},
	}));
	body.find(".import").on("click", () => {
		if (!digest || busy) return;
		run(1);
	});
	frappe.call({method: method + "settings"}).then(async response => {
		for (const key of Object.keys(controls)) await controls[key].set_value(response.message[key] || "");
		const saved = response.message.current_job ? JSON.stringify(response.message.current_job) : sessionStorage.getItem(storageKey);
		if (saved) {
			const previous = JSON.parse(saved);
			for (const [key, value] of Object.entries(previous.defaults || {})) if (controls[key]) await controls[key].set_value(value);
			fileUrl = previous.file_url;
			run(previous.apply, previous);
		}
	}).catch(() => { body.find(".upload").prop("disabled", true); });
};
