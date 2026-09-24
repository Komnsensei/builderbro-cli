// Creating a file with many lines to test if size is a factor.
// This file will have approximately 2100 lines.
let i = 0;
while (i < 2090) {
  console.log("This is line " + i + " of the large test file.");
  i++;
}
console.log("Final line of large_test.mjs.");
