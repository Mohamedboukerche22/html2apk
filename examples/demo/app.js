let n = 0;
const out = document.getElementById("count");
document.getElementById("tap").addEventListener("click", () => {
  n += 1;
  out.textContent = n;
  if (n === 5) document.getElementById("greeting").textContent = "Nice tapping!";
});
