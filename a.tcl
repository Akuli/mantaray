package require Tk
package require Ttk

ttk::panedwindow .pw

ttk::treeview .pw.tv
.pw.tv insert {} end
.pw add .pw.tv

ttk::frame .pw.pane
.pw add .pw.pane
ttk::entry .pw.pane.e
pack .pw.pane.e
pack .pw

text .pw.text
pack .pw.text -in .pw.pane

set t [expr [clock seconds] + 2]
while {[clock seconds] < $t} {
    update
}

destroy .
