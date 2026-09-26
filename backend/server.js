require("dotenv").config();

const express = require("express");
const cors = require("cors");
const routes = require("./routes");

const app = express();


app.use(cors());
app.use(express.json());

app.use("/api", routes);

app.get("/", (req, res) => {
  res.send("Behavioral AI Backend is running");
});

app.listen(5000, () => {
  console.log("Server running on port 5000");
});