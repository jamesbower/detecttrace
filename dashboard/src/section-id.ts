// A section is a heading's id on a page and its fragment in the page's address, as in
// `/help#evidence-completeness`. Only a plain id is kept: anything else in an address could name
// another page or origin. The router reads sections from addresses and the registry checks a help
// section's id against the same rule; this module imports neither, so neither import makes a cycle.
export const SECTION_ID_PATTERN = /^[a-z][a-z-]*$/;

/** The shell's main element, the skip link's target. No section may take its id. */
export const MAIN_ID = "main-content";
