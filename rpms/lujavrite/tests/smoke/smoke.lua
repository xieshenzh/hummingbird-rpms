--
-- Copyright (c) 2023 Red Hat, Inc.
--
-- Licensed under the Apache License, Version 2.0 (the "License");
-- you may not use this file except in compliance with the License.
-- You may obtain a copy of the License at
--
--     http://www.apache.org/licenses/LICENSE-2.0
--
-- Unless required by applicable law or agreed to in writing, software
-- distributed under the License is distributed on an "AS IS" BASIS,
-- WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
-- See the License for the specific language governing permissions and
-- limitations under the License.
--

local lujavrite = require "lujavrite"

-- Determine Java home to use
java_home = os.getenv("JAVA_HOME")
if java_home == nil then
   java_home = "/usr/lib/jvm/jre"
end

print("JVM initially initialized? " .. tostring(lujavrite.initialized()))

-- Initialize JVM
lujavrite.init(java_home .. "/lib/server/libjvm.so", "-ea", "-esa")

print("JVM initialized now? " .. tostring(lujavrite.initialized()))

-- System.getProperty(key)
function get_property(key)
   return lujavrite.call(
      "java/lang/System", "getProperty",
      "(Ljava/lang/String;)Ljava/lang/String;",
      key
   )
end

-- System.setProperty(key, value)
function set_property(key, value)
   return lujavrite.call(
      "java/lang/System", "setProperty",
      "(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;",
      key, value
   )
end

local java_version = get_property("java.version")
print("Java version is " .. java_version)

set_property("foo", "bar")
print("foo is " .. get_property("foo"))

local java_nil = lujavrite.call("java/lang/String", "valueOf", "(Ljava/lang/Object;)Ljava/lang/String;", nil)
print("nil in Lua is " .. java_nil .. " in Java")
