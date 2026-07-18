<template>
  <div id="app">
    <router-view />
  </div>
</template>

<script lang="ts">
  import {defineComponent} from 'vue';

  export default defineComponent({
    name: 'App',
  });
</script>

<style>
  #app {
    width: 100vw;
    height: 100vh;
    overflow: hidden;
  }

  body {
    margin: 0;
    padding: 0;
  }
</style>
